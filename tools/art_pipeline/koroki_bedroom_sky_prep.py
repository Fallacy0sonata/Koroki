"""Prep the living-sky assets for the bedroom scene from the chosen plates:
  - sky.png        : sky_42002 as the base plate (skyline + glow)
  - moon.png       : soft radial cut of the moon from clouds_43001 (own layer — must NOT
                     drift with clouds)
  - cloud_<n>.png  : individual cloud masses from clouds_43001, generous 24px feather —
                     dark surround blends invisibly over the dark sky base, so each cloud
                     becomes an independently drifting sprite (desync philosophy: many
                     small movers, not one cloud plate)

Output -> assets/world/bedroom_scene/ + preview sheet in data/art_previews.
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = r"C:/Users/Shinn/Desktop/Koroki"
PREV = os.path.join(ROOT, "data", "art_previews")
SRCD = os.path.join(PREV, "bedroom_scene")
DEST = os.path.join(ROOT, "assets", "world", "bedroom_scene")

MOON = {"center": (335, 178), "r_core": 60, "r_glow": 100}

# blob masks (ellipse bboxes) on clouds_43001, heavy feather; each becomes one sprite
CLOUDS = {
    "cloud_a": (30, 340, 440, 740),     # big left mass
    "cloud_b": (400, 520, 840, 830),    # bottom-center mass
    "cloud_c": (820, 340, 1216, 780),   # right lower stack
    "cloud_d": (940, 40, 1216, 360),    # right upper stack
    "wisp_a": (570, 210, 850, 300),     # small mid wisp
    "wisp_b": (760, 60, 1000, 130),     # tiny high wisp
}
FEATHER = 24


def estimate_sky_bg(arr):
    """Per-row background color of the cloud plate's sky, linear-fit from the mostly-clear
    top rows (darkest 45% of pixels per row, moon region excluded), extrapolated to all rows.
    Needed because blob-feather cuts carry source-sky halos that show as smudgy boxes when
    clouds drift over brighter regions of the destination sky."""
    h, w, _ = arr.shape
    lum = arr.mean(axis=2)
    rows = []
    for y in range(0, 300):
        row = arr[y].copy()
        l = lum[y].copy()
        l[230:470] = 1e9 if 90 <= y <= 270 else l[230:470]  # exclude moon+halo columns
        idx = np.argsort(l)[: int(w * 0.45)]
        rows.append((y, np.median(row[idx], axis=0)))
    ys = np.array([r[0] for r in rows], dtype=np.float64)
    vals = np.array([r[1] for r in rows], dtype=np.float64)
    bg = np.zeros((h, 3))
    for c in range(3):
        a, b = np.polyfit(ys, vals[:, c], 1)
        bg[:, c] = a * np.arange(h) + b
    return bg


def cloud_alpha(arr, bg, t1=16.0, t2=50.0):
    """Alpha from color distance to the estimated sky background (smoothstep t1..t2),
    with a morphological open to drop star specks."""
    dist = np.abs(arr - bg[:, None, :]).max(axis=2)
    a = np.clip((dist - t1) / (t2 - t1), 0, 1)
    a = (a * a * (3 - 2 * a) * 255).astype("uint8")
    im = Image.fromarray(a, "L").filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))
    return im.filter(ImageFilter.GaussianBlur(2.0))


def soft_ellipse_cut(im, bbox, feather, key_alpha=None):
    m = Image.new("L", im.size, 0)
    ImageDraw.Draw(m).ellipse(bbox, fill=255)
    m = m.filter(ImageFilter.GaussianBlur(feather))
    if key_alpha is not None:
        m = Image.fromarray(
            (np.asarray(m).astype(np.float32) * np.asarray(key_alpha).astype(np.float32) / 255.0
             ).astype("uint8"), "L")
    out = im.convert("RGBA")
    out.putalpha(m)
    return out.crop((max(0, bbox[0] - 2 * feather), max(0, bbox[1] - 2 * feather),
                     min(im.width, bbox[2] + 2 * feather), min(im.height, bbox[3] + 2 * feather)))


def main():
    os.makedirs(DEST, exist_ok=True)
    sky = Image.open(os.path.join(SRCD, "sky_42002.png")).convert("RGB")
    sky.save(os.path.join(DEST, "sky.png"))

    clouds = Image.open(os.path.join(SRCD, "clouds_43001.png")).convert("RGB")

    # moon: radial soft alpha (core opaque, glow falls off)
    cx, cy = MOON["center"]
    rc, rg = MOON["r_core"], MOON["r_glow"]
    yy, xx = np.ogrid[:clouds.height, :clouds.width]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    a = np.clip((rg - d) / (rg - rc), 0, 1) ** 1.5 * 255
    moon = clouds.convert("RGBA")
    moon.putalpha(Image.fromarray(a.astype("uint8"), "L"))
    moon = moon.crop((cx - rg, cy - rg, cx + rg, cy + rg))
    moon.save(os.path.join(DEST, "moon.png"))
    print("moon.png", moon.size, flush=True)

    arr = np.asarray(clouds).astype(np.float64)
    bg = estimate_sky_bg(arr)
    key = cloud_alpha(arr, bg)
    for name, bbox in CLOUDS.items():
        spr = soft_ellipse_cut(clouds, bbox, FEATHER, key_alpha=key)
        spr.save(os.path.join(DEST, f"{name}.png"))
        print(f"{name}.png", spr.size, flush=True)

    # preview: sky base + moon + all cloud sprites at rough scene positions
    comp = sky.convert("RGBA").copy()
    comp.alpha_composite(moon, (150, 60))
    offsets = {"cloud_a": (-60, 320), "cloud_b": (330, 480), "cloud_c": (760, 300),
               "cloud_d": (880, 10), "wisp_a": (500, 170), "wisp_b": (700, 30)}
    for name, bbox in CLOUDS.items():
        spr = Image.open(os.path.join(DEST, f"{name}.png"))
        comp.alpha_composite(spr, offsets[name])
    comp.convert("RGB").save(os.path.join(PREV, "sky_assembly_preview.jpg"), quality=90)
    print(f"PREVIEW -> {os.path.join(PREV, 'sky_assembly_preview.jpg')}", flush=True)


if __name__ == "__main__":
    main()
