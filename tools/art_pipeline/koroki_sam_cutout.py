"""Generic SAM cutout from the bedroom shell: name -> box + point prompts -> full-canvas
RGBA sprite (opaque only inside the mask), saved to assets/world/bedroom_scene/<name>.png.
Full-canvas output means the scene config just places it at design center — no offset math.

First use: bed_front — the bed's near side + foot of the duvet, layered ABOVE Koroki at
the bed spot so she's tucked IN the bed, not sitting on a sticker of it.

Usage: koroki_sam_cutout.py <cutname> [preview|save] [--cpu]
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = r"C:/Users/Shinn/Desktop/Koroki"
PREV = os.path.join(ROOT, "data", "art_previews")
SHELL = os.path.join(PREV, "bedroom_scene", "shell_41003.png")
CKPT = os.path.join(ROOT, "tools", "models", "sam", "sam_vit_b_01ec64.pth")
DEST = os.path.join(ROOT, "assets", "world", "bedroom_scene")

CUTS = {
    # near side of the bed: duvet front edge + foot + visible frame, occludes her legs
    "bed_front": {
        "box": (170, 555, 850, 832),
        "pos": [(430, 650), (600, 620), (350, 700), (700, 690), (520, 760)],
        "neg": [(400, 500), (300, 520),          # pillows / upper duvet (stay behind her)
                (100, 690), (60, 560),           # nightstand + lamp
                (150, 800), (900, 760), (750, 810),  # floor
                (880, 580)],                     # window sill / wall right of bed
        "feather": 1.5,
    },
}


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "bed_front"
    mode = sys.argv[2] if len(sys.argv) > 2 else "preview"
    spec = CUTS[name]

    import torch
    from segment_anything import sam_model_registry, SamPredictor
    device = "cuda" if torch.cuda.is_available() and "--cpu" not in sys.argv else "cpu"
    print(f"loading SAM vit_b on {device}...", flush=True)
    sam = sam_model_registry["vit_b"](checkpoint=CKPT).to(device)
    predictor = SamPredictor(sam)

    im = Image.open(SHELL).convert("RGB")
    arr = np.asarray(im)
    predictor.set_image(arr)

    pos, neg = spec["pos"], spec["neg"]
    pts = np.array(pos + neg, dtype=np.float32)
    lbl = np.array([1] * len(pos) + [0] * len(neg), dtype=np.int32)
    masks, scores, _ = predictor.predict(point_coords=pts, point_labels=lbl,
                                         box=np.array(spec["box"], dtype=np.float32),
                                         multimask_output=True)
    best = int(np.argmax(scores))
    m = masks[best].copy()
    x0, y0, x1, y1 = spec["box"]
    clip = np.zeros_like(m)
    clip[y0:y1, x0:x1] = True
    m &= clip
    print(f"{name} score {scores[best]:.3f} area {m.mean()*100:.2f}%", flush=True)

    # keep only the largest connected component — SAM leaves stray speckles (floor bits)
    from scipy.ndimage import label
    lab, n = label(m)
    if n > 1:
        sizes = np.bincount(lab.ravel())
        sizes[0] = 0
        m = lab == int(np.argmax(sizes))

    a = Image.fromarray((m * 255).astype("uint8"), "L")
    a = a.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    a = a.filter(ImageFilter.GaussianBlur(spec.get("feather", 1.5)))

    if mode == "save":
        out = im.convert("RGBA")
        out.putalpha(a)
        os.makedirs(DEST, exist_ok=True)
        out.save(os.path.join(DEST, f"{name}.png"))
        print(f"SPRITE -> {os.path.join(DEST, name + '.png')}", flush=True)

    ov = arr.copy()
    sel = np.asarray(a) > 100
    ov[sel] = (ov[sel] * 0.35 + np.array([60, 120, 255]) * 0.65).astype("uint8")
    ovi = Image.fromarray(ov)
    d = ImageDraw.Draw(ovi)
    d.rectangle(spec["box"], outline=(255, 240, 40))
    for x, y in pos:
        d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(255, 240, 40))
    for x, y in neg:
        d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(255, 40, 40))
    outp = os.path.join(PREV, f"sam_cutout_{name}.jpg")
    ovi.save(outp, quality=92)
    print(f"OVERLAY -> {outp}", flush=True)


if __name__ == "__main__":
    main()
