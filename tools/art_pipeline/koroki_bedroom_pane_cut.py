"""Cut the window panes of the chosen bedroom shell (shell_41003) to alpha, so the live
sky/cloud/moon/city layers show through. Panes are hand-traced polygons (the frame is
sketchy + in perspective); mullions/frames stay opaque.

Modes:
  overlay  -> red overlay of the pane polygons on the shell, for eyeball iteration
  cut      -> write assets/world/bedroom_scene/shell.png (alpha panes, feathered)
              + composite-check previews (over magenta, over the real sky plate)
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = r"C:/Users/Shinn/Desktop/Koroki"
PREV = os.path.join(ROOT, "data", "art_previews")
SHELL = os.path.join(PREV, "bedroom_scene", "shell_41003.png")
SKY = os.path.join(PREV, "bedroom_scene", "sky_42002.png")
DEST = os.path.join(ROOT, "assets", "world", "bedroom_scene")

# pane polygons on the 1216x832 shell — iterate with `overlay` mode
PANES = {
    # left window (behind the bed headboard), two panes split by the vertical mullion
    "left_a": [(24, 28), (252, 60), (254, 442), (16, 458)],
    "left_b": [(282, 70), (390, 96), (390, 420), (282, 432)],
    # lower-left window section (by the nightstand) — split around the table lamp,
    # which stands IN FRONT of the glass (same rule as the bed notch)
    "left_low_a": [(0, 452), (60, 456), (58, 602), (0, 608)],
    "left_low_b": [(60, 456), (128, 462), (126, 500), (62, 496)],
    # right window wall: transom band above the horizontal bar, then two tall panes.
    # right_a routes AROUND the bed's duvet corner (the bed is shell content — no hole in it).
    "right_transom": [(642, 8), (1128, 0), (1128, 122), (644, 138)],
    "right_transom_l": [(616, 10), (640, 8), (642, 132), (618, 136)],
    # thin glass sliver between the curtain edge and the window's left post
    "right_a0": [(614, 180), (632, 178), (632, 560), (614, 556)],
    "right_a": [(648, 164), (930, 152), (934, 600), (844, 604), (836, 552), (648, 536)],
    "right_b": [(962, 158), (1128, 150), (1132, 596), (966, 602)],
    # glass strip right of right_b, before the far curtain
    "right_edge": [(1132, 150), (1160, 144), (1164, 586), (1136, 592)],
    # baked city peeking below the far-right curtain, near the floor
    "right_edge_low": [(1164, 556), (1216, 538), (1216, 648), (1168, 604)],
}
FEATHER = 1.6


def draw_overlay():
    im = Image.open(SHELL).convert("RGB")
    ov = im.copy()
    dr = ImageDraw.Draw(ov, "RGBA")
    for name, poly in PANES.items():
        dr.polygon(poly, fill=(255, 40, 40, 110), outline=(255, 255, 0, 255))
        dr.text((poly[0][0] + 6, poly[0][1] + 6), name, fill=(255, 255, 0, 255))
    out = os.path.join(PREV, "pane_overlay.jpg")
    ov.save(out, quality=92)
    print(f"OVERLAY -> {out}", flush=True)


GLASS_RING_PX = 13     # inner shadow width inside each pane hole
GLASS_RING_ALPHA = 0.5  # peak opacity of the ring at the frame edge
GLASS_RING_COLOR = (14, 20, 34)


SAM_MASK = os.path.join(PREV, "bedroom_scene", "pane_mask.png")


def cut(use_sam=False):
    os.makedirs(DEST, exist_ok=True)
    im = Image.open(SHELL).convert("RGBA")
    w, h = im.size
    if use_sam:
        # pixel-accurate glass mask from koroki_sam_mask.py (SAM box+point prompts)
        hole = Image.open(SAM_MASK).convert("L").resize((w, h))
    else:
        hole = Image.new("L", (w, h), 0)
        dr = ImageDraw.Draw(hole)
        for poly in PANES.values():
            dr.polygon(poly, fill=255)
    hole = hole.filter(ImageFilter.GaussianBlur(FEATHER))
    hole_np = np.asarray(hole).astype(np.float32)

    # glass-depth ring: a soft dark gradient just INSIDE each hole, baked into the shell
    # as semi-opaque pixels — grounds the live sky "behind glass" instead of behind a
    # paper cutout. ring = hole minus eroded hole, blurred, clipped to the hole.
    eroded = hole.filter(ImageFilter.MinFilter(2 * GLASS_RING_PX + 1))
    ring = np.clip(hole_np - np.asarray(eroded).astype(np.float32), 0, 255)
    ring = np.asarray(Image.fromarray(ring.astype("uint8"), "L")
                      .filter(ImageFilter.GaussianBlur(GLASS_RING_PX * 0.55))).astype(np.float32)
    ring *= hole_np / 255.0                       # never outside the glass
    ring_a = ring / 255.0 * GLASS_RING_ALPHA

    rgba = np.asarray(im).astype(np.float32)
    base_alpha = 255.0 - hole_np
    shell_a = np.maximum(base_alpha, ring_a * 255.0)
    # where the ring shows, shell pixels are the dark glass color
    mix = (ring_a * 255.0 > base_alpha)[..., None]
    color = np.where(mix, np.array(GLASS_RING_COLOR, dtype=np.float32), rgba[:, :, :3])
    out = np.concatenate([color, shell_a[..., None]], axis=2).astype("uint8")
    Image.fromarray(out, "RGBA").save(os.path.join(DEST, "shell.png"))
    im = Image.open(os.path.join(DEST, "shell.png"))

    # checks: magenta reveals the holes; sky composite approximates the real scene
    mag = Image.new("RGBA", (w, h), (255, 0, 200, 255))
    mag.alpha_composite(im)
    mag.convert("RGB").save(os.path.join(PREV, "pane_cut_magenta.jpg"), quality=92)
    sky = Image.open(SKY).convert("RGBA").resize((w, h))
    comp = sky.copy()
    comp.alpha_composite(im)
    comp.convert("RGB").save(os.path.join(PREV, "pane_cut_skycomp.jpg"), quality=92)
    print(f"CUT -> {os.path.join(DEST, 'shell.png')} + previews", flush=True)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "overlay"
    if mode == "cut-sam":
        cut(use_sam=True)
    elif mode == "cut":
        cut()
    else:
        draw_overlay()
