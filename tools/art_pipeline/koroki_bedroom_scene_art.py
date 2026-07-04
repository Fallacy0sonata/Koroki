"""Layered bedroom scene — art foundation (compositional, per master queue):
  A. EMPTY bedroom shell candidates (no furniture — furniture comes from the cut library
     as independent layers; window panes get hand-cut to alpha next step).
  B. Night sky/city BASE plates (skyline + glow, minimal clouds) for behind the glass.
  C. Clouds-only plates (clouds on near-black sky) — drift as a separate SCREEN-blend
     layer over the base so the sky itself has desynced internal motion.

Locked style engine: FLUX.1-dev fp8 + Sketch Pad LoRA str 1.0, digrngbrsh, guidance 3.5,
euler/simple 26 steps (same as the P_* room pool).

Output: data/art_previews/bedroom_scene/{shell,sky,clouds}_<seed>.png + contact sheet.
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
DEST = os.path.join(PREV, "bedroom_scene")
LORA = "sketch_sketchpad_concept.safetensors"
STYLE = ("digrngbrsh, loose painterly concept-art illustration, hand-painted, "
         "moody warm-and-cool palette")

JOBS = [
    # (kind, seed, w, h, prompt)
    *[("shell", 41000 + i, 1216, 832,
       "a completely empty cozy high-rise condo bedroom at night, empty room with no "
       "furniture at all, bare wooden floorboards, bare walls, a huge floor-to-ceiling "
       "window wall with a distant neon city skyline at night, window mullions, warm dim "
       "ambient interior lighting from unseen lamps, " + STYLE) for i in range(4)],
    *[("sky", 42000 + i, 1216, 832,
       "a vast night city skyline seen from high up in a tower apartment, deep blue-violet "
       "night sky, distant skyscrapers with tiny glowing windows, warm city glow on the "
       "horizon, a few faint stars, clear sky with almost no clouds, no interior, no window "
       "frame, no foreground objects, " + STYLE) for i in range(3)],
    *[("clouds", 43000 + i, 1216, 832,
       "soft moody night clouds drifting on a nearly black dark sky, only clouds, dim "
       "moonlit cloud wisps, no city, no buildings, no ground, no stars, very dark "
       "background, " + STYLE) for i in range(2)],
]


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


def gen(prompt, seed, w, h, steps=26, guid=3.5):
    wf = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "flux1-dev-fp8.safetensors"}},
        "10": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["4", 0], "lora_name": LORA, "strength_model": 1.0}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": prompt}},
        "26": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["6", 0], "guidance": guid}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": ""}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": 1.0,
              "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
              "model": ["10", 0], "positive": ["26", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "kbedscene", "images": ["8", 0]}},
    }
    pid = post("/prompt", {"prompt": wf})["prompt_id"]
    for _ in range(500):
        try:
            h2 = json.loads(get(f"/history/{pid}"))
        except Exception:
            h2 = {}
        if pid in h2 and h2[pid].get("outputs", {}).get("9", {}).get("images"):
            info = h2[pid]["outputs"]["9"]["images"][0]
            return os.path.join(OUT, info["subfolder"], info["filename"])
        time.sleep(3)
    return None


def main():
    os.makedirs(DEST, exist_ok=True)
    if not wait_server():
        print("FATAL: ComfyUI not reachable", flush=True)
        return
    results = []
    for kind, seed, w, h, prompt in JOBS:
        if not wait_server():
            print("FATAL: ComfyUI gone mid-batch", flush=True)
            return
        t0 = time.time()
        r = gen(prompt, seed, w, h)
        if not r:
            print(f"FAIL {kind} {seed}", flush=True)
            continue
        im = Image.open(r).convert("RGB")
        name = f"{kind}_{seed}"
        im.save(os.path.join(DEST, f"{name}.png"))
        results.append((name, im))
        print(f"OK {name}  {time.time()-t0:5.1f}s", flush=True)

    cols = 3
    cw, chh = 400, 288
    rows = (len(results) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cw, rows * (chh + 22) + 6), (18, 16, 20))
    dr = ImageDraw.Draw(sheet)
    for k, (name, im) in enumerate(results):
        t = im.copy()
        t.thumbnail((cw - 6, chh))
        r_, c_ = divmod(k, cols)
        sheet.paste(t, (c_ * cw + 3, r_ * (chh + 22) + 20))
        dr.text((c_ * cw + 8, r_ * (chh + 22) + 4), name, fill=(235, 210, 160))
    out = os.path.join(PREV, "bedroom_scene_candidates.jpg")
    sheet.save(out, quality=90)
    print(f"SHEET -> {out}", flush=True)
    print("BEDROOM SCENE ART DONE", flush=True)


if __name__ == "__main__":
    main()
