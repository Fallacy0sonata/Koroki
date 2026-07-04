"""Painterly restyle of Koroki via FLUX+SketchPad img2img. Denoise sweep to find the point that
restyles her rendering (painterly) WITHOUT losing her face/design. One sprite (koroki_neutral),
levels 0.35/0.45/0.55/0.65, shown next to the original. Lock the value, then batch all sprites.
"""
import json, time, urllib.request, os
from PIL import Image, ImageDraw
BASE="http://127.0.0.1:8188"; OUT="tools/ComfyUI/output"; INP="tools/ComfyUI/input"
ROOT=r"C:/Users/Shinn/Desktop/Koroki"
SPR=os.path.join(ROOT,"clients","web","assets","koroki_sprites")
PREV=r"C:/Users/Shinn/Desktop/Koroki/data/art_previews"
def get(p): return urllib.request.urlopen(BASE+p,timeout=20).read()
def post(p,o):
    r=urllib.request.Request(BASE+p,data=json.dumps(o).encode(),headers={"Content-Type":"application/json"}); return json.loads(urllib.request.urlopen(r,timeout=60).read())
for _ in range(60):
    try: get("/system_stats"); break
    except Exception: time.sleep(3)
oi=json.loads(get("/object_info"));

# prep source: composite onto cream so img2img sees full RGB (no transparency artifacts)
src=Image.open(os.path.join(SPR,"koroki_neutral.png")).convert("RGBA")
W,H=src.size
cream=Image.new("RGBA",(W,H),(244,240,232,255)); cream.alpha_composite(src)
cream.convert("RGB").save(os.path.join(INP,"koroki_src.png"))
orig=cream.convert("RGB")

LORA="sketch_sketchpad_concept.safetensors"
KPROMPT=("a cute girl with grey fox ears with cream inner fur, long wavy ash-grey hair, half-lidded crimson-red "
 "droopy eyes, small red x-shaped hairpins and a red broken-heart hairpin on her bangs, wearing an oversized "
 "cream knit sweater, kneeling, cozy warm soft lighting, digrngbrsh, loose painterly concept-art illustration, "
 "hand-painted, plain soft background")

def gen(denoise,seed,steps=28,guid=3.5):
    wf={"4":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"flux1-dev-fp8.safetensors"}},
     "20":{"class_type":"LoadImage","inputs":{"image":"koroki_src.png"}},
     "22":{"class_type":"VAEEncode","inputs":{"pixels":["20",0],"vae":["4",2]}},
     "10":{"class_type":"LoraLoaderModelOnly","inputs":{"model":["4",0],"lora_name":LORA,"strength_model":1.0}},
     "6":{"class_type":"CLIPTextEncode","inputs":{"clip":["4",1],"text":KPROMPT}},
     "26":{"class_type":"FluxGuidance","inputs":{"conditioning":["6",0],"guidance":guid}},
     "7":{"class_type":"CLIPTextEncode","inputs":{"clip":["4",1],"text":""}},
     "3":{"class_type":"KSampler","inputs":{"seed":seed,"steps":steps,"cfg":1.0,"sampler_name":"euler","scheduler":"simple","denoise":denoise,"model":["10",0],"positive":["26",0],"negative":["7",0],"latent_image":["22",0]}},
     "8":{"class_type":"VAEDecode","inputs":{"samples":["3",0],"vae":["4",2]}},
     "9":{"class_type":"SaveImage","inputs":{"filename_prefix":"krestyle","images":["8",0]}}}
    pid=post("/prompt",{"prompt":wf})["prompt_id"]
    for _ in range(500):
        try: h2=json.loads(get(f"/history/{pid}"))
        except: h2={}
        if pid in h2 and h2[pid].get("outputs",{}).get("9",{}).get("images"):
            info=h2[pid]["outputs"]["9"]["images"][0]; return os.path.join(OUT,info["subfolder"],info["filename"])
        time.sleep(3)
    return None

results=[("original",orig)]
for i,dn in enumerate([0.35,0.45,0.55,0.65]):
    r=gen(dn,6600+i)
    if not r: print("FAIL",dn,flush=True); continue
    im=Image.open(r).convert("RGB"); im.save(os.path.join(PREV,f"krestyle_{dn}.png"))
    results.append((f"denoise {dn}",im)); print("OK",dn,flush=True)

cols=len(results); cw=300; ch=int(cw*H/W)
sheet=Image.new("RGB",(cols*cw,ch+24),(24,22,26)); dr=ImageDraw.Draw(sheet)
for k,(lab,im) in enumerate(results):
    t=im.copy(); t.thumbnail((cw-6,ch)); ox=k*cw+3; dr.text((ox,4),lab,fill=(235,210,160)); sheet.paste(t,(ox,22))
sheet.save(os.path.join(PREV,"koroki_restyle_sheet.jpg"),quality=92)
print("RESTYLE SWEEP DONE",flush=True)
