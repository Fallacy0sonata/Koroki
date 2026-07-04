"""SAM-based pane masking for the bedroom shell (replaces hand-traced polygons — owner
verdict 2026-07-02: hand polygons = low-quality masks; use a real segmentation model).

Runs in .venv_diffsinger (torch 2.8 + torchvision live there; main .venv is locked for
the Brain). Checkpoint: tools/models/sam/sam_vit_b_01ec64.pth (Meta, free, local).

Each glass region gets positive points inside the glass and shared negative points on
everything that must survive (frame strokes, mullions, bed, lamp, curtains, wall,
ceiling — the band above the right window's bar is NOT glass per owner's annotation).
SAM returns pixel-accurate boundaries that follow the painted strokes.

Modes:
  preview -> data/art_previews/sam_pane_overlay.jpg (green = mask)
  save    -> data/art_previews/bedroom_scene/pane_mask.png (union, uint8 L)
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = r"C:/Users/Shinn/Desktop/Koroki"
PREV = os.path.join(ROOT, "data", "art_previews")
SHELL = os.path.join(PREV, "bedroom_scene", "shell_41003.png")
CKPT = os.path.join(ROOT, "tools", "models", "sam", "sam_vit_b_01ec64.pth")
MASK_OUT = os.path.join(PREV, "bedroom_scene", "pane_mask.png")

# shared negatives: everything glass must NOT bleed into
NEG_COMMON = [
    (265, 250),   # left window mullion
    (948, 360),   # right window mullion
    (890, 142),   # horizontal frame bar
    (860, 60),    # band above the bar — NOT glass (owner annotation)
    (500, 60),    # ceiling smear
    (450, 180),   # corner wall
    (560, 300),   # corner curtain
    (1185, 320),  # far-right curtain
    (400, 600),   # bed
    (750, 585),   # duvet corner
    (330, 470),   # headboard
    (95, 545),    # nightstand lamp (stands in front of the glass)
    (100, 690),   # nightstand
    (900, 720),   # floor
    (1210, 700),  # floor right
]

# box prompt per pane (SAM is far more reliable box-constrained on stylized art;
# point-only prompting bled across the whole painting) + positive points inside.
# Masks are hard-clipped to their box.
#
# KNOWN LIMITATION (owner-reviewed 2026-07-02, accepted as good enough): SAM treats
# baked buildings inside the glass as foreground "things", so several tower clusters
# stayed on the shell instead of being cut (owner circled: right_a's tall tower group,
# the mid towers, the bottom dark buildings, the bridge building bottom-right, one small
# tower in left_a). They read as static near-city over the live drifting sky, which is
# physically plausible depth — but they ARE misses, not intent. To flip any of them into
# the cut: add a positive point on that building to its pane's "pos" list and re-run.
REGIONS = {
    "left_a":   {"box": (14, 24, 258, 462),   "pos": [(130, 240), (80, 360), (200, 140)]},
    "left_b":   {"box": (278, 66, 394, 436),  "pos": [(335, 240), (330, 140), (340, 360)]},
    "left_low": {"box": (0, 448, 132, 614),   "pos": [(30, 520), (42, 580)]},
    "right_a0": {"box": (610, 174, 636, 564), "pos": [(623, 300), (624, 420)]},
    "right_a":  {"box": (644, 148, 938, 608), "pos": [(780, 340), (700, 240), (880, 460)]},
    "right_b":  {"box": (958, 144, 1136, 606), "pos": [(1040, 340), (1000, 240), (1080, 480)]},
    "right_c":  {"box": (1128, 140, 1168, 596), "pos": [(1146, 300), (1148, 470)]},
}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "preview"
    import torch
    from segment_anything import sam_model_registry, SamPredictor

    device = "cuda" if torch.cuda.is_available() and "--cpu" not in sys.argv else "cpu"
    print(f"loading SAM vit_b on {device}...", flush=True)
    sam = sam_model_registry["vit_b"](checkpoint=CKPT).to(device)
    predictor = SamPredictor(sam)

    im = Image.open(SHELL).convert("RGB")
    arr = np.asarray(im)
    predictor.set_image(arr)
    print("image embedded", flush=True)

    union = np.zeros(arr.shape[:2], dtype=bool)
    for name, spec in REGIONS.items():
        pos = spec["pos"]
        box = np.array(spec["box"], dtype=np.float32)
        pts = np.array(pos + NEG_COMMON, dtype=np.float32)
        lbl = np.array([1] * len(pos) + [0] * len(NEG_COMMON), dtype=np.int32)
        masks, scores, _ = predictor.predict(point_coords=pts, point_labels=lbl,
                                             box=box, multimask_output=True)
        best = int(np.argmax(scores))
        m = masks[best].copy()
        # hard clip to the box — glass never exists outside its frame
        x0, y0, x1, y1 = spec["box"]
        clip = np.zeros_like(m)
        clip[y0:y1, x0:x1] = True
        m &= clip
        # fill interior holes: SAM excludes baked IN-GLASS objects (moon, far towers)
        # as "things", but they're painted ON the glass and must be cut too. Anything
        # not connected to the box border is interior glass content; the bed corner
        # and the lamp DO touch the border, so they survive.
        from scipy.ndimage import binary_fill_holes
        sub = m[y0:y1, x0:x1]
        m[y0:y1, x0:x1] = binary_fill_holes(sub)
        print(f"{name:9s} score {scores[best]:.3f}  area {m.mean()*100:5.2f}%", flush=True)
        union |= m

    # cleanup: open to drop specks, close to seal pinholes
    mimg = Image.fromarray((union * 255).astype("uint8"), "L")
    mimg = mimg.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(3))

    if mode == "save":
        mimg.save(MASK_OUT)
        print(f"MASK -> {MASK_OUT}", flush=True)

    ov = arr.copy()
    sel = np.asarray(mimg) > 127
    ov[sel] = (ov[sel] * 0.35 + np.array([40, 255, 90]) * 0.65).astype("uint8")
    ovi = Image.fromarray(ov)
    d = ImageDraw.Draw(ovi)
    for name, spec in REGIONS.items():
        d.rectangle(spec["box"], outline=(255, 240, 40))
        for x, y in spec["pos"]:
            d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(255, 240, 40))
    for x, y in NEG_COMMON:
        d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(255, 40, 40))
    out = os.path.join(PREV, "sam_pane_overlay.jpg")
    ovi.save(out, quality=92)
    print(f"OVERLAY -> {out}", flush=True)


if __name__ == "__main__":
    main()
