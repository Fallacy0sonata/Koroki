"""Root-cause fix for the furniture-cutout massacre.
The FLUX raws are good; isnet-anime (a CHARACTER segmenter) erased the furniture.
Re-cut the SAME raws two ways and compare:
  A) white-key flood-fill from corners (deterministic, no model, for plain-bg objects)
  B) isnet-general-use (general-object matting) if it downloads past SSL
Drop plushie (user: skip). Pieces: ac, bookshelf, beanbag, plant, lamp.
"""
import os, ssl
ssl._create_default_https_context = ssl._create_unverified_context  # rembg model dl SSL bypass
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUTDIR = r"C:/Users/Shinn/Desktop/Koroki/tools/ComfyUI/output"
PREV = r"C:/Users/Shinn/Desktop/Koroki/data/art_previews"
RAWS = [  # (label, filename) in generation order, plushie dropped
    ("ac",       "kfluxf_00001_.png"),
    ("bookshelf","kfluxf_00002_.png"),
    ("beanbag",  "kfluxf_00003_.png"),
    ("plant",    "kfluxf_00004_.png"),
    ("lamp",     "kfluxf_00005_.png"),
]

def white_key(im, thresh=38, feather=1.4, erode=1):
    """Flood-fill background from the 4 corners; alpha=0 there, 255 on the object."""
    rgb = im.convert("RGB")
    work = rgb.copy()
    KEY = (0, 254, 1)  # sentinel unlikely to occur
    w, h = work.size
    for xy in [(1,1),(w-2,1),(1,h-2),(w-2,h-2)]:
        ImageDraw.floodfill(work, xy, KEY, thresh=thresh)
    arr = np.asarray(work)
    is_bg = (arr[:,:,0]==KEY[0]) & (arr[:,:,1]==KEY[1]) & (arr[:,:,2]==KEY[2])
    alpha = np.where(is_bg, 0, 255).astype("uint8")
    a = Image.fromarray(alpha, "L")
    if erode:  # pull the edge in a touch to kill the white halo fringe
        a = a.filter(ImageFilter.MinFilter(2*erode+1))
    if feather:
        a = a.filter(ImageFilter.GaussianBlur(feather))
    out = rgb.convert("RGBA")
    out.putalpha(a)
    bbox = out.getbbox()
    return out.crop(bbox) if bbox else out

def general_matte(im):
    from rembg import new_session, remove
    sess = new_session("isnet-general-use")
    cut = remove(im.convert("RGBA"), session=sess)
    bbox = cut.getbbox()
    return cut.crop(bbox) if bbox else cut

def checker(w, h, s=20):
    img = Image.new("RGB", (w, h), (205,205,205)); d = ImageDraw.Draw(img)
    for y in range(0,h,s):
        for x in range(0,w,s):
            if (x//s+y//s)%2: d.rectangle((x,y,x+s,y+s), fill=(165,165,165))
    return img

gen_ok = True
try:
    _ = general_matte(Image.new("RGB",(64,64),(255,255,255)))
    print("isnet-general-use available", flush=True)
except Exception as e:
    gen_ok = False
    print("isnet-general-use UNAVAILABLE:", repr(e)[:140], flush=True)

wk_cuts, gm_cuts = [], []
for label, fn in RAWS:
    p = os.path.join(OUTDIR, fn)
    im = Image.open(p).convert("RGB")
    wk_cuts.append((label, white_key(im)))
    if gen_ok:
        try: gm_cuts.append((label, general_matte(im)))
        except Exception as e: print("gm fail", label, repr(e)[:80], flush=True); gm_cuts.append((label, None))
    print("cut", label, flush=True)

# Build comparison sheet: rows = pieces, cols = [white-key, general]
cell = 300
cols = 2 if gen_ok else 1
rows = len(RAWS)
sheet = Image.new("RGB", (cols*cell, rows*cell+30), (26,26,30)); dr = ImageDraw.Draw(sheet)
dr.text((8,4), "white-key", fill=(160,235,180))
if gen_ok: dr.text((cell+8,4), "isnet-general-use", fill=(160,200,235))
for r,(label,_) in enumerate(RAWS):
    oy = r*cell+26
    # white-key
    wk = wk_cuts[r][1].copy(); wk.thumbnail((cell-16,cell-30))
    bg = checker(wk.width,wk.height); bg.paste(wk,(0,0),wk)
    sheet.paste(bg,(6,oy)); dr.text((8,oy+cell-16),label,fill=(235,235,235))
    if gen_ok and gm_cuts[r][1] is not None:
        gm = gm_cuts[r][1].copy(); gm.thumbnail((cell-16,cell-30))
        bg2 = checker(gm.width,gm.height); bg2.paste(gm,(0,0),gm)
        sheet.paste(bg2,(cell+6,oy))
sheet.save(os.path.join(PREV,"furn_cutout_compare.jpg"), quality=90)
print("CUTOUT COMPARE DONE", flush=True)
