"""Dedicated keyboard-foreground layer for the webcam overlay.

Generates a standalone keyboard (no character) in the locked airy pen, then
cuts it to clean alpha via white-key flood fill (the furniture precedent) —
a real puppet part with hard edges, so composites never show paste seams.
"""
import json
import os
import time
import urllib.request

import numpy as np
from PIL import Image

BASE = "http://127.0.0.1:8188"
ROOT = r"C:/Users/Shinn/Desktop/Koroki"
OUT = os.path.join(ROOT, "tools", "ComfyUI", "output")
PREV = os.path.join(ROOT, "data", "art_previews", "webcam_style_probe")

CKPT = "Illustrious-XL-v1.0.safetensors"
SKETCH_LORA = "sketch_chaotic_lineart.safetensors"

# Layer parts must be generated ALONE — the flood-cut keeps everything
# non-background, so a "keyboard on desk" render smuggles the whole desk in.
POS = (
    "no humans, a single computer keyboard, nothing else, isolated object, "
    "plain pure white background, viewed from the front slightly above, "
    "pale pastel colors, flat color, thin delicate lineart, sketch, "
    "minimal shading, simple, cozy"
)
NEG = (
    "1girl, person, hands, desk, table, monitor, mouse, cable, text, letters, "
    "watermark, signature, blurry, shiny, glossy, high contrast, hdr, "
    "complex background, multiple objects"
)
SEEDS = [55001, 55002, 55003]


def get(p, timeout=20):
    return urllib.request.urlopen(BASE + p, timeout=timeout).read()


def post(p, o):
    r = urllib.request.Request(BASE + p, data=json.dumps(o).encode(),
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read())


def gen(seed):
    wf = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "11": {"class_type": "LoraLoader", "inputs": {
            "model": ["4", 0], "clip": ["4", 1], "lora_name": SKETCH_LORA,
            "strength_model": 0.4, "strength_clip": 0.4}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["11", 1], "text": POS}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["11", 1], "text": NEG}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1216, "height": 512, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": 24, "cfg": 4.2, "sampler_name": "euler_ancestral",
            "scheduler": "normal", "denoise": 1.0, "model": ["11", 0],
            "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "kbd_layer", "images": ["8", 0]}},
    }
    pid = post("/prompt", {"prompt": wf})["prompt_id"]
    for _ in range(300):
        try:
            h = json.loads(get(f"/history/{pid}"))
        except Exception:
            h = {}
        if pid in h and h[pid].get("outputs", {}).get("9", {}).get("images"):
            info = h[pid]["outputs"]["9"]["images"][0]
            return os.path.join(OUT, info["subfolder"], info["filename"])
        time.sleep(2)
    return None


def flood_cut(img: Image.Image, tol: int = 26) -> Image.Image:
    """White-key flood fill from the border → alpha (furniture precedent)."""
    arr = np.array(img.convert("RGB")).astype(np.int16)
    h, w, _ = arr.shape
    bg = arr[0, 0].astype(np.int16)
    near_bg = (np.abs(arr - bg).sum(axis=2) < tol * 3)
    # flood from all border pixels
    from collections import deque
    visited = np.zeros((h, w), dtype=bool)
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if near_bg[y, x] and not visited[y, x]:
                visited[y, x] = True
                q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if near_bg[y, x] and not visited[y, x]:
                visited[y, x] = True
                q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and near_bg[ny, nx]:
                visited[ny, nx] = True
                q.append((ny, nx))
    alpha = np.where(visited, 0, 255).astype(np.uint8)
    out = img.convert("RGBA")
    out.putalpha(Image.fromarray(alpha))
    return out


def main():
    os.makedirs(PREV, exist_ok=True)
    cuts = []
    for seed in SEEDS:
        r = gen(seed)
        if not r:
            print(f"FAIL seed {seed}", flush=True)
            continue
        img = Image.open(r)
        cut = flood_cut(img)
        p = os.path.join(PREV, f"kbd_layer_{seed}.png")
        cut.save(p)
        cuts.append((seed, cut))
        print(f"OK kbd {seed}", flush=True)

    # Composite demo: r3-left plate + best keyboard cut, hard alpha, no seams.
    base = Image.open(os.path.join(PREV, "life_chin_rest_52001.png")).convert("RGBA")
    W, H = base.size
    for seed, cut in cuts:
        kb = cut.copy()
        scale = W / kb.width
        kb = kb.resize((W, int(kb.height * scale)))
        frame = base.copy()
        frame.alpha_composite(kb, (0, H - int(kb.height * 0.82)))
        frame.convert("RGB").save(os.path.join(PREV, f"composite_clean_{seed}.jpg"), quality=92)
    print("KEYBOARD LAYER DONE", flush=True)


if __name__ == "__main__":
    main()
