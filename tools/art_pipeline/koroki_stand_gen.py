"""Regen the broken stand_* sprites — step 1: candidate standing bodies.

txt2img on Illustrious-XL + koroki_lora_v2 (trigger `koroki`, str 0.85, CLIP+model —
character LoRA, unlike the FLUX style LoRA which is model-only). One NEUTRAL standing
body, N seeds, same outfit as the kneeling set (oversized cream knit sweater, barefoot)
so the home wardrobe stays coherent. Ash-grey hair prompted AT GENERATION (txt2img obeys
color prompts — no recolor pass needed if it takes). Heavy anti-crop framing guards:
the old stand_* set failed with heads cut off.

User picks the winning body from the sheet -> face-inpaint expressions onto it (step 2)
-> 0.35 SketchPad pass + alpha-transfer cut (step 3, same treatment as the kneeling set).

Output: data/art_previews/stand_candidates/cand_<seed>.png + stand_candidates_sheet.jpg
"""
import json
import os
import time
import urllib.request

from PIL import Image, ImageDraw

BASE = "http://127.0.0.1:8188"
ROOT = r"C:/Users/Shinn/Desktop/Koroki"
OUT = os.path.join(ROOT, "tools", "ComfyUI", "output")
PREV = os.path.join(ROOT, "data", "art_previews")
CAND = os.path.join(PREV, "stand_candidates")

CKPT = "Illustrious-XL-v1.0.safetensors"
LORA = "koroki_lora_v2.safetensors"
LORA_STR = 0.85

_POS_BASE = (
    "koroki, 1girl, solo, standing, full body, head to toe, feet visible, barefoot, "
    "oversized cream cable-knit sweater, sweater slightly past hips, bare legs, "
    "long wavy ash-grey hair, grey fox ears with cream inner fur, fluffy grey fox tail, "
    "crimson half-lidded droopy eyes, small red broken-heart hairpin, white x-shaped hairpins, "
    "soft neutral expression, relaxed arms, cozy warm soft lighting, "
    "simple background, flat plain cream background, masterpiece, best quality"
)
_NEG_BASE = (
    "cropped, out of frame, close-up, portrait, upper body, cowboy shot, head out of frame, "
    "cut off, text, letters, watermark, signature, blurry, extra limbs, bad hands, bad feet, "
    "extra tails, lowres, jpeg artifacts"
)

# Round 2: round-1 bodies skewed petite/young vs canonical (TALL statuesque early-20s,
# must match the kneeling set's adult proportions). Add proportion guards.
ROUNDS = {
    "1": {"pos": _POS_BASE, "neg": _NEG_BASE, "seeds": [31001, 31002, 31003, 31004, 31005, 31006]},
    "2": {
        "pos": _POS_BASE.replace(
            "soft neutral expression",
            "tall slender woman, mature adult proportions, long legs, "
            "soft neutral expression with a knowing cat-like calm",
        ),
        "neg": _NEG_BASE + ", chibi, child, loli, petite, short, big head, toddler",
        "seeds": [32001, 32002, 32003, 32004, 32005, 32006],
    },
}
_round = "1"
POS, NEG, SEEDS = _POS_BASE, _NEG_BASE, ROUNDS["1"]["seeds"]


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


def gen(seed, steps=28, cfg=5.0):
    wf = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "10": {"class_type": "LoraLoader", "inputs": {
            "model": ["4", 0], "clip": ["4", 1], "lora_name": LORA,
            "strength_model": LORA_STR, "strength_clip": LORA_STR}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["10", 1], "text": POS}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["10", 1], "text": NEG}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler_ancestral",
            "scheduler": "normal", "denoise": 1.0, "model": ["10", 0],
            "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "kstand", "images": ["8", 0]}},
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
    os.makedirs(CAND, exist_ok=True)
    if not wait_server():
        print("FATAL: ComfyUI not reachable", flush=True)
        return
    results = []
    for seed in SEEDS:
        if not wait_server():
            print("FATAL: ComfyUI gone mid-batch", flush=True)
            return
        t0 = time.time()
        r = gen(seed)
        if not r:
            print(f"FAIL seed {seed}", flush=True)
            continue
        im = Image.open(r).convert("RGB")
        im.save(os.path.join(CAND, f"cand_{seed}.png"))
        results.append((seed, im))
        print(f"OK seed {seed}  {time.time()-t0:5.1f}s", flush=True)

    cw, chh = 300, 440
    sheet = Image.new("RGB", (len(results) * cw, chh + 26), (18, 16, 20))
    dr = ImageDraw.Draw(sheet)
    for k, (seed, im) in enumerate(results):
        t = im.copy()
        t.thumbnail((cw - 6, chh))
        sheet.paste(t, (k * cw + 3, 24))
        dr.text((k * cw + 8, 6), str(seed), fill=(235, 210, 160))
    out = os.path.join(PREV, f"stand_candidates_sheet_r{_round}.jpg")
    sheet.save(out, quality=90)
    print(f"SHEET -> {out}", flush=True)
    print("STAND CANDIDATES DONE", flush=True)


if __name__ == "__main__":
    import sys
    _round = sys.argv[1] if len(sys.argv) > 1 else "1"
    r = ROUNDS[_round]
    POS, NEG, SEEDS = r["pos"], r["neg"], r["seeds"]
    main()
