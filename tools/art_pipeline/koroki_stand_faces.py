"""Stand sprite step 2: face-inpaint the expression set onto the chosen body.

Body = cand_32006 (user pick 2026-07-02: face age-vibe 18-20 is the anchor; black
oversized knit accepted — outfits not locked). Neutral keeps the base face as-is;
the other five expressions are inpainted with a soft ellipse mask over the face only
(SetLatentNoiseMask, denoise 0.65, Illustrious + koroki LoRA) so hair/pins/ears/body
stay byte-identical outside the mask — the same-body-face-swap contract.

Output: data/art_previews/stand_faces/stand_<expr>.png + stand_faces_sheet.jpg
(full body + zoomed face strip for judging).
"""
import json
import os
import time
import urllib.request

from PIL import Image, ImageDraw, ImageFilter

BASE = "http://127.0.0.1:8188"
ROOT = r"C:/Users/Shinn/Desktop/Koroki"
OUT = os.path.join(ROOT, "tools", "ComfyUI", "output")
INP = os.path.join(ROOT, "tools", "ComfyUI", "input")
PREV = os.path.join(ROOT, "data", "art_previews")
FACES = os.path.join(PREV, "stand_faces")
BODY = os.path.join(PREV, "stand_candidates", "cand_32006.png")

CKPT = "Illustrious-XL-v1.0.safetensors"
LORA = "koroki_lora_v2.safetensors"
LORA_STR = 0.85

# face region on cand_32006 (832x1216): ellipse, soft edge
FACE_BOX = (360, 122, 516, 278)   # x0, y0, x1, y1
MASK_FEATHER = 12

CHAR = (
    "koroki, 1girl, solo, standing, black oversized cable-knit sweater, "
    "long wavy ash-grey hair, grey fox ears, crimson eyes, "
    "small red broken-heart hairpin, {expr}, masterpiece, best quality"
)
EXPRS = {
    "happy":     "bright happy smile, cheerful, sparkling crimson eyes, light blush",
    "smug":      "smug teasing grin, half-lidded crimson eyes, confident cat-like smile",
    "sleepy":    "sleepy drowsy face, eyes nearly closed, relaxed soft mouth",
    "surprised": "wide open crimson eyes, surprised, parted lips, blush",
    "pout":      "pouting, puffed cheeks, sulky half-lidded droopy crimson eyes",
}
NEG = (
    "cropped, out of frame, text, watermark, blurry, extra limbs, bad hands, lowres, "
    "jpeg artifacts, deformed face, extra eyes"
)
SEED = 5550
DENOISE = 0.65


def get(p, timeout=20):
    return urllib.request.urlopen(BASE + p, timeout=timeout).read()


def post(p, o):
    r = urllib.request.Request(BASE + p, data=json.dumps(o).encode(),
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read())


def wait_server(max_s=180):
    t0 = time.time()
    while time.time() - t0 < max_s:
        try:
            get("/system_stats")
            return True
        except Exception:
            time.sleep(3)
    return False


def make_mask(size):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).ellipse(FACE_BOX, fill=255)
    return m.filter(ImageFilter.GaussianBlur(MASK_FEATHER))


def gen(expr_text, seed):
    wf = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "10": {"class_type": "LoraLoader", "inputs": {
            "model": ["4", 0], "clip": ["4", 1], "lora_name": LORA,
            "strength_model": LORA_STR, "strength_clip": LORA_STR}},
        "20": {"class_type": "LoadImage", "inputs": {"image": "kstand_base.png"}},
        "21": {"class_type": "LoadImage", "inputs": {"image": "kstand_facemask.png"}},
        "23": {"class_type": "ImageToMask", "inputs": {"image": ["21", 0], "channel": "red"}},
        "22": {"class_type": "VAEEncode", "inputs": {"pixels": ["20", 0], "vae": ["4", 2]}},
        "24": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["22", 0], "mask": ["23", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["10", 1], "text": CHAR.format(expr=expr_text)}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["10", 1], "text": NEG}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": 30, "cfg": 5.0, "sampler_name": "euler_ancestral",
            "scheduler": "normal", "denoise": DENOISE, "model": ["10", 0],
            "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["24", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "kface", "images": ["8", 0]}},
    }
    pid = post("/prompt", {"prompt": wf})["prompt_id"]
    for _ in range(500):
        try:
            h = json.loads(get(f"/history/{pid}"))
        except Exception:
            h = {}
        if pid in h and h[pid].get("outputs", {}).get("9", {}).get("images"):
            info = h[pid]["outputs"]["9"]["images"][0]
            return os.path.join(OUT, info["subfolder"], info["filename"])
        time.sleep(2)
    return None


def main():
    os.makedirs(FACES, exist_ok=True)
    if not wait_server():
        print("FATAL: ComfyUI not reachable", flush=True)
        return

    body = Image.open(BODY).convert("RGB")
    body.save(os.path.join(INP, "kstand_base.png"))
    make_mask(body.size).convert("RGB").save(os.path.join(INP, "kstand_facemask.png"))
    body.save(os.path.join(FACES, "stand_neutral.png"))  # base face IS the neutral
    print("OK neutral    (base face kept)", flush=True)

    results = {"neutral": body}
    for i, (expr, expr_text) in enumerate(EXPRS.items()):
        if not wait_server():
            print("FATAL: ComfyUI gone mid-batch", flush=True)
            return
        t0 = time.time()
        r = gen(expr_text, SEED + i)
        if not r:
            print(f"FAIL {expr}", flush=True)
            continue
        im = Image.open(r).convert("RGB")
        im.save(os.path.join(FACES, f"stand_{expr}.png"))
        results[expr] = im
        print(f"OK {expr:10s}  {time.time()-t0:5.1f}s", flush=True)

    # sheet: full body row + zoomed face row
    order = ["neutral"] + list(EXPRS)
    cw, chh = 260, 380
    fx0, fy0, fx1, fy1 = FACE_BOX
    pad = 26
    zw, zh = cw, int(cw * (fy1 - fy0 + 2 * pad) / (fx1 - fx0 + 2 * pad))
    sheet = Image.new("RGB", (len(order) * cw, chh + zh + 44), (18, 16, 20))
    dr = ImageDraw.Draw(sheet)
    for k, expr in enumerate(order):
        im = results.get(expr)
        if im is None:
            continue
        t = im.copy()
        t.thumbnail((cw - 6, chh))
        sheet.paste(t, (k * cw + 3, 22))
        dr.text((k * cw + 8, 4), expr, fill=(235, 210, 160))
        face = im.crop((fx0 - pad, fy0 - pad, fx1 + pad, fy1 + pad)).resize((zw - 6, zh))
        sheet.paste(face, (k * cw + 3, chh + 40))
    out = os.path.join(PREV, "stand_faces_sheet.jpg")
    sheet.save(out, quality=92)
    print(f"SHEET -> {out}", flush=True)
    print("STAND FACES DONE", flush=True)


if __name__ == "__main__":
    main()
