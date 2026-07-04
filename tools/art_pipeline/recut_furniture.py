"""Re-cut furniture raws with a robust white-key: seed the flood from corners AND a dense grid of
inset points, but ONLY where the pixel is background-colored (bright + low-saturation). This gets
past the Sketch Pad paper-edge border ring that trapped the corner-only flood, without eating objects.
Operates on saved raws (no regen). Rewrites cut/ + a fresh checker sheet.
"""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
ROOT=r"C:/Users/Shinn/Desktop/Koroki"
FDIR=os.path.join(ROOT,"assets","flux_style_farm","furniture")
RAW=os.path.join(FDIR,"raw"); CUT=os.path.join(FDIR,"cut"); os.makedirs(CUT,exist_ok=True)
PREV=r"C:/Users/Shinn/Desktop/Koroki/data/art_previews"

def bgish(px):
    r,g,b=px[0],px[1],px[2]; return min(r,g,b)>190 and (max(r,g,b)-min(r,g,b))<42

def white_key(im,thresh=44,feather=1.3,erode=1):
    rgb=im.convert("RGB"); work=rgb.copy(); px=rgb.load(); KEY=(0,254,1); w,h=work.size
    seeds=[(2,2),(w-3,2),(2,h-3),(w-3,h-3)]
    for frac in (0.03,0.06,0.10):
        ix=int(w*frac); iy=int(h*frac)
        for t in range(0,13):
            x=int(ix+(w-1-2*ix)*t/12); y=int(iy+(h-1-2*iy)*t/12)
            seeds += [(x,iy),(x,h-1-iy),(ix,y),(w-1-ix,y)]
    seen=set()
    for sx,sy in seeds:
        sx=max(0,min(w-1,sx)); sy=max(0,min(h-1,sy))
        if (sx,sy) in seen: continue
        seen.add((sx,sy))
        if not bgish(px[sx,sy]): continue          # only flood from true background pixels
        try: ImageDraw.floodfill(work,(sx,sy),KEY,thresh=thresh)
        except Exception: pass
    arr=np.asarray(work)
    is_bg=(arr[:,:,0]==KEY[0])&(arr[:,:,1]==KEY[1])&(arr[:,:,2]==KEY[2])
    a=Image.fromarray(np.where(is_bg,0,255).astype("uint8"),"L")
    if erode: a=a.filter(ImageFilter.MinFilter(2*erode+1))
    if feather: a=a.filter(ImageFilter.GaussianBlur(feather))
    out=rgb.convert("RGBA"); out.putalpha(a); bbox=out.getbbox()
    return out.crop(bbox) if bbox else out

def checker(w,h,s=18):
    img=Image.new("RGB",(w,h),(205,205,205)); d=ImageDraw.Draw(img)
    for y in range(0,h,s):
        for x in range(0,w,s):
            if (x//s+y//s)%2: d.rectangle((x,y,x+s,y+s),fill=(165,165,165))
    return img

names=sorted(f[:-4] for f in os.listdir(RAW) if f.endswith(".png"))
cuts=[]
for n in names:
    im=Image.open(os.path.join(RAW,f"{n}.png")).convert("RGB")
    c=white_key(im); c.save(os.path.join(CUT,f"{n}.png")); cuts.append((n,c))
    # report residual bg fraction (alpha>0 area vs bbox) as a rough cleanliness check
    a=np.asarray(c.split()[-1]); frac=(a>10).mean()
    print(f"{n:16s} kept {frac*100:5.1f}% opaque", flush=True)

cols=5; import math; rows=math.ceil(len(cuts)/cols); cell=240
sheet=Image.new("RGB",(cols*cell,rows*cell+6),(26,26,30)); dr=ImageDraw.Draw(sheet)
for k,(n,im) in enumerate(cuts):
    t=im.copy(); t.thumbnail((cell-14,cell-26)); bg=checker(t.width,t.height); bg.paste(t,(0,0),t)
    r,c=divmod(k,cols); ox=c*cell+7; oy=r*cell+5; sheet.paste(bg,(ox,oy)); dr.text((ox+3,oy+cell-16),n,fill=(235,235,235))
sheet.save(os.path.join(PREV,"furniture_recut_sheet.jpg"),quality=88)
print("RECUT DONE",flush=True)
