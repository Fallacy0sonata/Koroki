"""Stand sprite step 3: painterly 0.35 pass + cut, matching the kneeling set's treatment.

- Base alpha: rembg isnet-anime on stand_neutral (a CHARACTER on flat bg — the case that
  segmenter is actually for; it only fails on furniture). All six share the body, so the
  neutral's alpha serves the whole set via alpha_transfer_cut.
- Restyle: FLUX + Sketch Pad LoRA img2img @0.35, same seed per expression, same recipe
  as the kneeling set (koroki_restyle_batch), prompt adapted to the standing/black-knit body.
- Cut: alpha_transfer_cut from koroki_restyle_batch (dilate + bg-veto, NO bbox crop).

Output: data/art_previews/stand_final/{raw,cut}/stand_<expr>.png + stand_final_sheet.jpg
"""
import os
import sys
import time

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from koroki_restyle_batch import (  # noqa: E402
    BASE, OUT, INP, PREV, ROOM, LORA, alpha_transfer_cut, get, post, wait_server,
)

ROOT = r"C:/Users/Shinn/Desktop/Koroki"
SRC = os.path.join(PREV, "stand_faces")
FINAL = os.path.join(PREV, "stand_final")

PROMPT = (
    "a cute girl with grey fox ears with cream inner fur, long wavy ash-grey hair, {expr}, "
    "small red heart hairpin on her bangs, wearing an oversized black cable-knit sweater, "
    "standing, bare legs, barefoot, cozy warm soft lighting, digrngbrsh, loose painterly "
    "concept-art illustration, hand-painted, plain soft background"
)
EXPRS = {
    "neutral":   "calm half-lidded crimson-red eyes, soft neutral expression",
    "happy":     "crimson-red eyes, bright happy smile, cheerful",
    "smug":      "half-lidded crimson-red eyes, smug teasing grin",
    "sleepy":    "crimson-red eyes nearly closed, sleepy drowsy expression",
    "surprised": "wide open crimson-red eyes, surprised parted lips",
    "pout":      "half-lidded crimson-red droopy eyes, pouting puffed cheeks",
}
DENOISE = 0.35
SEED0 = 8800


def gen(src_name, expr_text, seed, steps=28, guid=3.5):
    wf = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "flux1-dev-fp8.safetensors"}},
        "20": {"class_type": "LoadImage", "inputs": {"image": src_name}},
        "22": {"class_type": "VAEEncode", "inputs": {"pixels": ["20", 0], "vae": ["4", 2]}},
        "10": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["4", 0], "lora_name": LORA, "strength_model": 1.0}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": PROMPT.format(expr=expr_text)}},
        "26": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["6", 0], "guidance": guid}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": ""}},
        "3": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": 1.0,
              "sampler_name": "euler", "scheduler": "simple", "denoise": DENOISE,
              "model": ["10", 0], "positive": ["26", 0], "negative": ["7", 0], "latent_image": ["22", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "kstandfx", "images": ["8", 0]}},
    }
    import json
    pid = post("/prompt", {"prompt": wf})["prompt_id"]
    for _ in range(500):
        try:
            h = json.loads(get(f"/history/{pid}"))
        except Exception:
            h = {}
        if pid in h and h[pid].get("outputs", {}).get("9", {}).get("images"):
            info = h[pid]["outputs"]["9"]["images"][0]
            return os.path.join(OUT, info["subfolder"], info["filename"])
        time.sleep(3)
    return None


def main():
    os.makedirs(os.path.join(FINAL, "raw"), exist_ok=True)
    os.makedirs(os.path.join(FINAL, "cut"), exist_ok=True)

    # base alpha via rembg isnet-anime on the neutral body
    from rembg import remove, new_session
    session = new_session("isnet-anime")
    neutral = Image.open(os.path.join(SRC, "stand_neutral.png")).convert("RGB")
    base_rgba = remove(neutral, session=session)
    base_rgba.save(os.path.join(FINAL, "stand_base_alpha.png"))
    a = np.asarray(base_rgba.split()[-1])
    print(f"BASE ALPHA opaque {(a>10).mean()*100:4.1f}%", flush=True)

    if not wait_server():
        print("FATAL: ComfyUI not reachable", flush=True)
        return

    results = {}
    for i, (expr, expr_text) in enumerate(EXPRS.items()):
        src = Image.open(os.path.join(SRC, f"stand_{expr}.png")).convert("RGB")
        src.save(os.path.join(INP, f"ks_{expr}.png"))
        if not wait_server():
            print("FATAL: ComfyUI gone mid-batch", flush=True)
            return
        t0 = time.time()
        r = gen(f"ks_{expr}.png", expr_text, SEED0 + i)
        if not r:
            print(f"FAIL {expr}", flush=True)
            continue
        im = Image.open(r).convert("RGB")
        im.save(os.path.join(FINAL, "raw", f"stand_{expr}.png"))
        cut = alpha_transfer_cut(im, base_rgba)
        cut.save(os.path.join(FINAL, "cut", f"stand_{expr}.png"))
        ca = np.asarray(cut.split()[-1])
        print(f"OK {expr:10s}  {time.time()-t0:5.1f}s  opaque {(ca>10).mean()*100:4.1f}%", flush=True)
        results[expr] = cut

    # sheet over the hero bedroom
    room = Image.open(ROOM).convert("RGB")
    cw, chh = 280, 470
    crop = room.crop((520, 60, 520 + int(cw * (832 - 60) / chh), 832)).resize((cw, chh))
    sheet = Image.new("RGB", (len(EXPRS) * cw, chh + 26), (18, 16, 20))
    dr = ImageDraw.Draw(sheet)
    for k, expr in enumerate(EXPRS):
        cell = crop.copy()
        spr = results.get(expr)
        if spr is not None:
            t = spr.copy()
            t.thumbnail((cw, chh - 12))
            cell.paste(t, ((cw - t.width) // 2, chh - t.height), t)
        sheet.paste(cell, (k * cw, 24))
        dr.text((k * cw + 8, 5), expr, fill=(235, 210, 160))
    out = os.path.join(PREV, "stand_final_sheet.jpg")
    sheet.save(out, quality=92)
    print(f"SHEET -> {out}", flush=True)
    print("STAND RESTYLE DONE", flush=True)


if __name__ == "__main__":
    main()
