"""Bigger Style-A (sketchy pen + ink + watercolor wash) farm for the LoRA dataset pool.
Full-bleed (no torn border / no signature). Varied content: rooms + prop/furniture vignettes,
varied time-of-day, so the LoRA learns STYLE not content. Full-res -> assets/flux_style_farm/.
User cherry-picks the winners; those become the FLUX style-LoRA training set.
"""
import json, time, urllib.request, os
from PIL import Image, ImageDraw
BASE="http://127.0.0.1:8188"; OUT="tools/ComfyUI/output"
ROOT=r"C:/Users/Shinn/Desktop/Koroki"
FARM=os.path.join(ROOT,"assets","flux_style_farm"); os.makedirs(FARM,exist_ok=True)
PREV=r"C:/Users/Shinn/Desktop/Koroki/data/art_previews"
def get(p): return urllib.request.urlopen(BASE+p,timeout=20).read()
def post(p,o):
    r=urllib.request.Request(BASE+p,data=json.dumps(o).encode(),headers={"Content-Type":"application/json"}); return json.loads(urllib.request.urlopen(r,timeout=60).read())
for _ in range(60):
    try: get("/system_stats"); break
    except Exception: time.sleep(3)
oi=json.loads(get("/object_info")); gtype="EmptySD3LatentImage" if "EmptySD3LatentImage" in oi else "EmptyLatentImage"

STYLE=("hand-drawn sketchy pen-and-ink illustration, drawn with a real dip pen, confident expressive ink linework, "
 "visible hand-inked outlines that wobble slightly and vary in weight, delicate cross-hatching and short hatch "
 "strokes for shading, loose gestural construction lines left visible, soft muted watercolor wash laid over the ink, "
 "warm cozy indie visual-novel palette, gentle paper grain and subtle bleed where the wash pools, atmospheric and "
 "intimate, imperfect charming hand-made feel, artistic storyboard concept sketch, illustrative sketchbook aesthetic, "
 "full-bleed composition filling the whole frame edge to edge, no paper border, no white frame, no signature, no text, "
 "absolutely not photorealistic, not a 3d render, not smooth clean vector art, not glossy, richly detailed but clearly "
 "hand-drawn by an artist")

# (name, prompt, w, h) -- rooms landscape; vignettes squarer
ROOMS=[
 ("bedroom_night","a cozy high-rise condo bedroom at night, low plush bed with rumpled blankets, wooden nightstand with a warm glowing lamp, tall bookshelf, small potted plant, floor-to-ceiling corner windows with a distant blurred neon city skyline, soft warm interior lighting, rug on wooden floorboards",1216,832),
 ("bedroom_morning","a cozy bedroom in soft morning light, unmade bed with soft white sheets, sheer curtains glowing with daylight, a plant on the windowsill, a small desk, warm gentle sunlight across wooden floor",1216,832),
 ("lounge_evening","a small cozy living-room lounge in the evening, deep plush sofa piled with cushions, low coffee table with a mug and a stack of books, bookshelf along the wall, warm string fairy-lights, glowing floor lamp, window with soft city glow, woven rug on wooden floor",1216,832),
 ("study_rain","a warm little study nook, wooden desk against a rain-streaked window, open notebook and a steaming mug of tea, small desk lamp, corkboard with pinned notes, shelves of books, dim cozy evening light, blurred city beyond the wet glass",1216,832),
 ("balcony_sunset","a small cozy apartment balcony at dusk overlooking a neon city skyline, potted plants, a little folding chair and tiny side table with a drink, string lights along the railing, warm golden-pink evening sky fading into city lights, gentle haze",1216,832),
 ("reading_night","a snug reading corner, big round floor beanbag and scattered floor cushions, low bookshelf, warm arched floor lamp leaning over, fairy-lights on the wall, soft blanket, small plant, wooden floor, window with faint night city glow",1216,832),
 ("kitchen_morning","a tiny cozy kitchenette in soft morning light, warm wooden cabinets, little counter with a kettle and mugs, hanging leafy potted plants, open shelves with jars and dishes, a small window with gentle daylight, homely lived-in warmth",1216,832),
 ("bathroom_warm","a cozy small bathroom at night, a freestanding tub, a couple of leafy plants, a warm candle and soft towels, a little window, warm intimate lamplight, steam in the air, wooden accents",1216,832),
 ("genkan_entry","a warm Japanese apartment entryway genkan at night, shoes lined up on a step, a coat on a hook, a small shelf with keys and a plant, warm overhead light, wooden floor and tiled step",1216,832),
 ("rooftop_dusk","a small cozy rooftop garden at dusk with a city skyline behind, potted plants and string lights, a low bench with cushions and a lantern, warm golden light fading to blue, gentle atmosphere",1216,832),
 ("windowseat_rain","a cozy cushioned window seat by a large rain-streaked window, soft pillows and a folded blanket, a mug and an open book, a small plant, warm lamp, blurred rainy city beyond, intimate evening mood",1216,832),
 ("sunroom_day","a bright plant-filled sunroom in the daytime, many leafy potted plants on shelves and floor, a rattan chair with cushions, big windows with soft daylight, wooden floor, calm airy warmth",1216,832),
 ("cafe_corner","a cozy corner of a tiny home cafe, a small wooden counter with a coffee machine and mugs, a chalkboard menu, hanging plants, warm pendant lights, a stool, homely inviting warmth",1216,832),
 ("tatami_night","a cozy traditional tatami room at night, a low wooden table with a warm lamp and tea, floor cushions, a paper lantern glowing, a shoji screen, a small potted plant, soft warm intimate light",1216,832),
]
VIGN=[
 ("prop_desk","a close-up vignette of a wooden desk with a warm desk lamp, an open notebook, a steaming mug, a small potted plant and a few scattered pens, cozy warm light, simple soft background",1024,1024),
 ("prop_windowsill","a close-up of a windowsill lined with small potted plants and a string of tiny fairy-lights, soft daylight through the glass, cozy and warm, simple background",1024,1024),
 ("prop_bookshelf","a close-up of a wooden bookshelf packed with colorful books, small trinkets and a tiny plant, warm cozy lighting, simple soft background",1024,1024),
 ("prop_bed","a plush bed with rumpled soft blankets and pillows, a folded throw, warm intimate lighting, cozy, simple soft background",1216,832),
 ("prop_armchair","a cozy plush reading armchair with a soft blanket draped over it and a cushion, a small side table with a mug, warm lamplight, simple soft background",1024,1024),
 ("prop_cushions","a cluster of soft floor cushions and a round beanbag around a low wooden table with a mug and a book, a warm rug, cozy warm lighting, simple soft background",1024,1024),
]
JOBS=ROOMS+VIGN

def gen(prompt,seed,w,h):
    wf={"4":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"flux1-dev-fp8.safetensors"}},
     "6":{"class_type":"CLIPTextEncode","inputs":{"clip":["4",1],"text":prompt}},
     "26":{"class_type":"FluxGuidance","inputs":{"conditioning":["6",0],"guidance":3.1}},
     "7":{"class_type":"CLIPTextEncode","inputs":{"clip":["4",1],"text":""}},
     "5":{"class_type":gtype,"inputs":{"width":w,"height":h,"batch_size":1}},
     "3":{"class_type":"KSampler","inputs":{"seed":seed,"steps":26,"cfg":1.0,"sampler_name":"euler","scheduler":"simple","denoise":1.0,"model":["4",0],"positive":["26",0],"negative":["7",0],"latent_image":["5",0]}},
     "8":{"class_type":"VAEDecode","inputs":{"samples":["3",0],"vae":["4",2]}},
     "9":{"class_type":"SaveImage","inputs":{"filename_prefix":"kstyleb","images":["8",0]}}}
    pid=post("/prompt",{"prompt":wf})["prompt_id"]
    for _ in range(500):
        try: h2=json.loads(get(f"/history/{pid}"))
        except: h2={}
        if pid in h2 and h2[pid].get("outputs",{}).get("9",{}).get("images"):
            info=h2[pid]["outputs"]["9"]["images"][0]; return os.path.join(OUT,info["subfolder"],info["filename"])
        time.sleep(3)
    return None

results=[]
for i,(name,scene,w,h) in enumerate(JOBS):
    r=gen(f"{scene}, {STYLE}",2000+i,w,h)
    if not r: print("FAIL",name,flush=True); continue
    im=Image.open(r).convert("RGB"); im.save(os.path.join(FARM,f"A_{name}.png"))
    results.append((name,im)); print("OK",name,flush=True)

cols=4; import math; rows=math.ceil(len(results)/cols); cw=340; ch=230
sheet=Image.new("RGB",(cols*cw,rows*(ch+20)+6),(24,22,26)); dr=ImageDraw.Draw(sheet)
for k,(lab,im) in enumerate(results):
    t=im.copy(); t.thumbnail((cw-8,ch)); r,c=divmod(k,cols); ox=c*cw+4; oy=r*(ch+20)+18
    dr.text((ox,oy-15),lab,fill=(235,210,160)); sheet.paste(t,(ox,oy))
sheet.save(os.path.join(PREV,"style_farm_big_sheet.jpg"),quality=86)
print("BIG STYLE FARM DONE ->",FARM,flush=True)
