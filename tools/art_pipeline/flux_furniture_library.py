"""Furniture library in the LOCKED Sketch Pad style. Gen each piece isolated on a plain pale bg,
then WHITE-KEY flood-fill cut (corners) -> layer-ready transparent PNG. trigger digrngbrsh, str 1.0.
Raws -> assets/flux_style_farm/furniture/raw/ ; cutouts -> .../furniture/cut/ ; checker sheet -> scratchpad.
"""
import json, time, urllib.request, os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
BASE="http://127.0.0.1:8188"; OUT="tools/ComfyUI/output"
ROOT=r"C:/Users/Shinn/Desktop/Koroki"
FDIR=os.path.join(ROOT,"assets","flux_style_farm","furniture")
RAW=os.path.join(FDIR,"raw"); CUT=os.path.join(FDIR,"cut")
for d in (RAW,CUT): os.makedirs(d,exist_ok=True)
PREV=r"C:/Users/Shinn/Desktop/Koroki/data/art_previews"
def get(p): return urllib.request.urlopen(BASE+p,timeout=20).read()
def post(p,o):
    r=urllib.request.Request(BASE+p,data=json.dumps(o).encode(),headers={"Content-Type":"application/json"}); return json.loads(urllib.request.urlopen(r,timeout=60).read())
for _ in range(60):
    try: get("/system_stats"); break
    except Exception: time.sleep(3)
oi=json.loads(get("/object_info")); gtype="EmptySD3LatentImage" if "EmptySD3LatentImage" in oi else "EmptyLatentImage"
LORA="sketch_sketchpad_concept.safetensors"; TRIG="digrngbrsh"
STYLE=(", single object centered, isolated on a plain flat pale-cream background, no scene, no walls, no floor, "
 "no cast shadow, soft even lighting, cozy indie painterly style")

PIECES={
 "bed":"a plush low double bed with a soft duvet and pillows, wooden frame",
 "sofa":"a deep plush two-seat sofa with cushions",
 "armchair":"a cozy plush reading armchair with a cushion",
 "bookshelf":"a tall wooden bookshelf filled with colorful books and a few trinkets",
 "floor_lamp":"an elegant warm-glowing floor lamp",
 "desk_lamp":"a small warm desk lamp",
 "potted_plant":"a large leafy green potted indoor plant",
 "small_plant":"a small potted succulent plant",
 "coffee_table":"a low round wooden coffee table with a mug and books on it",
 "desk":"a wooden writing desk with an open notebook and a mug",
 "nightstand":"a small wooden nightstand with a drawer",
 "wooden_chair":"a simple wooden chair",
 "rug":"a soft round woven rug seen from a slight angle",
 "beanbag":"a big round plush floor beanbag cushion",
 "floor_cushions":"a small pile of soft floor cushions",
 "heart_pillow":"a cute soft plush heart-shaped throw pillow in cream and dusty-rose",
 "wall_art":"a framed piece of cozy wall art, a small painting in a wooden frame",
 "string_lights":"a coiled string of warm fairy-lights",
 "ac_unit":"a modern white wall-mounted split air conditioner unit",
 "curtains":"a pair of soft hanging window curtains",
}
def gen(prompt,seed,w=1024,h=1024,steps=26,guid=3.5):
    wf={"4":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"flux1-dev-fp8.safetensors"}},
     "10":{"class_type":"LoraLoaderModelOnly","inputs":{"model":["4",0],"lora_name":LORA,"strength_model":1.0}},
     "6":{"class_type":"CLIPTextEncode","inputs":{"clip":["4",1],"text":prompt}},
     "26":{"class_type":"FluxGuidance","inputs":{"conditioning":["6",0],"guidance":guid}},
     "7":{"class_type":"CLIPTextEncode","inputs":{"clip":["4",1],"text":""}},
     "5":{"class_type":gtype,"inputs":{"width":w,"height":h,"batch_size":1}},
     "3":{"class_type":"KSampler","inputs":{"seed":seed,"steps":steps,"cfg":1.0,"sampler_name":"euler","scheduler":"simple","denoise":1.0,"model":["10",0],"positive":["26",0],"negative":["7",0],"latent_image":["5",0]}},
     "8":{"class_type":"VAEDecode","inputs":{"samples":["3",0],"vae":["4",2]}},
     "9":{"class_type":"SaveImage","inputs":{"filename_prefix":"kfurn","images":["8",0]}}}
    pid=post("/prompt",{"prompt":wf})["prompt_id"]
    for _ in range(500):
        try: h2=json.loads(get(f"/history/{pid}"))
        except: h2={}
        if pid in h2 and h2[pid].get("outputs",{}).get("9",{}).get("images"):
            info=h2[pid]["outputs"]["9"]["images"][0]; return os.path.join(OUT,info["subfolder"],info["filename"])
        time.sleep(3)
    return None

def white_key(im,thresh=40,feather=1.4,erode=1):
    rgb=im.convert("RGB"); work=rgb.copy(); KEY=(0,254,1); w,h=work.size
    for xy in [(1,1),(w-2,1),(1,h-2),(w-2,h-2)]:
        ImageDraw.floodfill(work,xy,KEY,thresh=thresh)
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

cuts=[]
for i,(name,desc) in enumerate(PIECES.items()):
    r=gen(desc+STYLE,4400+i)
    if not r: print("FAIL",name,flush=True); continue
    im=Image.open(r).convert("RGB"); im.save(os.path.join(RAW,f"{name}.png"))
    c=white_key(im); c.save(os.path.join(CUT,f"{name}.png")); cuts.append((name,c))
    print("OK",name,flush=True)

cols=5; import math; rows=math.ceil(len(cuts)/cols); cell=240
sheet=Image.new("RGB",(cols*cell,rows*cell+6),(26,26,30)); dr=ImageDraw.Draw(sheet)
for k,(n,im) in enumerate(cuts):
    t=im.copy(); t.thumbnail((cell-14,cell-26)); bg=checker(t.width,t.height); bg.paste(t,(0,0),t)
    r,c=divmod(k,cols); ox=c*cell+7; oy=r*cell+5; sheet.paste(bg,(ox,oy)); dr.text((ox+3,oy+cell-16),n,fill=(235,235,235))
sheet.save(os.path.join(PREV,"furniture_library_sheet.jpg"),quality=88)
print("FURNITURE LIBRARY DONE ->",CUT,flush=True)
