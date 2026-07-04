"""Hair recolor: warm brown -> canonical ash-grey, applied to the SOURCE sprites before
the 0.35 painterly pass. At 0.35 denoise img2img preserves color, so the prompt's
"ash-grey hair" never took — the source itself must be ash-grey first.

Mask: warm-hue, mid-value, mid-saturation pixels = hair (skin is brighter/pinker, the
sweater is near-neutral cream, the tail is already grey, eyes are high-sat red). All six
expression sprites share the same body+hair, so the same selection works across the set.

Recolor: luminance-preserving desaturation with a slight cool bias — shading survives,
color temperature moves to ash.

Run modes:
  preview  -> writes mask overlay + recolored neutral to data/art_previews for eyeballing
  apply    -> writes recolored kb_<expr>.png into ComfyUI input for the 0.35 batch
"""
import os
import sys

import numpy as np
from PIL import Image, ImageFilter

ROOT = r"C:/Users/Shinn/Desktop/Koroki"
SPR = os.path.join(ROOT, "assets", "koroki_sprites")
PREV = os.path.join(ROOT, "data", "art_previews")
INP = os.path.join(ROOT, "tools", "ComfyUI", "input")
CREAM = (244, 240, 232)

EXPRS = ["neutral", "happy", "smug", "sleepy", "surprised", "pout"]

# tuning
DESAT_KEEP = 0.18      # fraction of original chroma kept (0 = pure grey)
COOL_SHIFT = 4         # small blue bias, "ash" not "mouse"
LIGHTEN = 1.06         # ash-grey reads a touch lighter than the dark brown

# Spatial exclusion: her NECK's warm skin shadow overlaps hair in color space
# (sat 0.19-0.22 vs bangs 0.16-0.19 — measured, not separable by color rules), and
# desaturating it made FLUX invent a grey turtleneck. All six sprites share the same
# body geometry, so one fixed hand-traced polygon over the neck protects the whole
# set — traced tight so the hair lock curling past the collar stays recolorable.
# Polygon in source coords (832x1216).
NECK_POLY = [
    (440, 455), (488, 464), (514, 494), (530, 558), (524, 626),
    (472, 654), (424, 646), (397, 608), (399, 538), (419, 486),
]


def hair_mask(rgb_arr, alpha):
    """Boolean mask of hair pixels from an RGBA sprite array."""
    r = rgb_arr[:, :, 0].astype(np.float32)
    g = rgb_arr[:, :, 1].astype(np.float32)
    b = rgb_arr[:, :, 2].astype(np.float32)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    val = mx / 255.0
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    # hue in degrees (only need the warm sector)
    hue = np.zeros_like(mx)
    d = np.maximum(mx - mn, 1)
    is_r = (mx == r)
    is_g = (mx == g) & ~is_r
    hue[is_r] = (60 * ((g - b) / d) % 360)[is_r]
    hue[is_g] = (60 * ((b - r) / d) + 120)[is_g]

    warm = (hue < 55) | (hue > 340)
    m = (
        (alpha > 100)
        & warm
        & (sat > 0.14) & (sat < 0.62)   # cream sweater/tail below, red eyes/pins above
        & (val > 0.12) & (val < 0.88)   # skin highlights above
    )
    # red guard: eyes, pins, blush are high-sat true red — never touch
    m &= ~((sat > 0.45) & ((hue < 18) | (hue > 345)))
    # neck guard: fixed hand-traced polygon over her neck (shared body geometry)
    from PIL import ImageDraw as _ID
    guard = Image.new("L", (m.shape[1], m.shape[0]), 0)
    _ID.Draw(guard).polygon(NECK_POLY, fill=255)
    m &= ~(np.asarray(guard) > 0)
    return m


def refine(mask_bool, size):
    m = Image.fromarray((mask_bool * 255).astype("uint8"), "L")
    m = m.filter(ImageFilter.MaxFilter(3))     # close pinholes in shaded strands
    m = m.filter(ImageFilter.MinFilter(3))
    m = m.filter(ImageFilter.GaussianBlur(2.0))  # soft transition into skin/sweater
    return np.asarray(m).astype(np.float32) / 255.0


def recolor(im):
    arr = np.asarray(im.convert("RGBA")).copy()
    rgb = arr[:, :, :3].astype(np.float32)
    alpha = arr[:, :, 3]
    m = refine(hair_mask(arr[:, :, :3], alpha), im.size)[:, :, None]

    lum = (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2])[:, :, None]
    ash = lum * LIGHTEN + np.array([-COOL_SHIFT, 0.0, COOL_SHIFT])[None, None, :]
    new = rgb * DESAT_KEEP + ash * (1 - DESAT_KEEP)
    out = rgb * (1 - m) + new * m
    arr[:, :, :3] = np.clip(out, 0, 255).astype("uint8")
    return Image.fromarray(arr, "RGBA"), m[:, :, 0]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "preview"
    if mode == "preview":
        src = Image.open(os.path.join(SPR, "koroki_neutral.png"))
        rec, m = recolor(src)
        # side-by-side: original | mask overlay | recolored
        w, h = src.size
        over = np.asarray(src.convert("RGB")).copy()
        over[m > 0.4] = (over[m > 0.4] * 0.4 + np.array([255, 40, 40]) * 0.6).astype("uint8")
        sheet = Image.new("RGB", (w * 3, h), (30, 28, 32))
        sheet.paste(src.convert("RGB"), (0, 0))
        sheet.paste(Image.fromarray(over), (w, 0))
        bg = Image.new("RGBA", (w, h), CREAM + (255,))
        bg.alpha_composite(rec)
        sheet.paste(bg.convert("RGB"), (w * 2, 0))
        out = os.path.join(PREV, "ashgrey_preview.jpg")
        sheet.save(out, quality=92)
        print(f"PREVIEW -> {out}", flush=True)
    elif mode == "apply":
        for expr in EXPRS:
            src = Image.open(os.path.join(SPR, f"koroki_{expr}.png"))
            rec, _ = recolor(src)
            bg = Image.new("RGBA", src.size, CREAM + (255,))
            bg.alpha_composite(rec)
            bg.convert("RGB").save(os.path.join(INP, f"kb_{expr}.png"))
            print(f"APPLIED {expr}", flush=True)
        print("SOURCES READY (ComfyUI input kb_*.png overwritten with ash-grey)", flush=True)
    else:
        print("usage: koroki_ashgrey_recolor.py [preview|apply]")


if __name__ == "__main__":
    main()
