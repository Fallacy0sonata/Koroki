"""Stream-avatar style probe — finding her 'soft, effortless, hand-drawn' pen.

Owner verdict 2026-07-04 (docs/koroki_stream_avatar_spec.md): current LoRA render
is 7/10 — beautiful but effortful, generic-AI sheen. Target = the soft muted
sketch-flat feel of the reference pics: relaxing, almost effortless.

Six recipes on the SAME webcam-plate pose (seated at desk, upper body, facing
viewer), 2 seeds each. Owner picks from the sheet; winner becomes the locked pen
for all stream-avatar plates.
"""
import json
import os
import time
import urllib.request

from PIL import Image, ImageDraw

BASE = "http://127.0.0.1:8188"
ROOT = r"C:/Users/Shinn/Desktop/Koroki"
OUT = os.path.join(ROOT, "tools", "ComfyUI", "output")
PREV = os.path.join(ROOT, "data", "art_previews", "webcam_style_probe")

CKPT = "Illustrious-XL-v1.0.safetensors"
CHAR_LORA = "koroki_lora_v2.safetensors"
SKETCH_LORA = "sketch_chaotic_lineart.safetensors"

# The webcam plate pose — consistent across all recipes so only the PEN varies.
_POSE = (
    "koroki, 1girl, solo, sitting at desk, upper body, facing viewer, "
    "hands near keyboard, white headphones around neck, "
    "oversized cream cable-knit sweater, "
    "long wavy ash-grey hair, grey fox ears with cream inner fur, "
    "crimson half-lidded droopy eyes, small red broken-heart hairpin, "
    "soft neutral expression with a knowing cat-like calm, "
    "simple background, plain cream background"
)
_NEG_COMMON = (
    "text, letters, watermark, signature, blurry, extra limbs, bad hands, "
    "lowres, jpeg artifacts, extra tails, chibi, child"
)

# recipe: (label, extra_pos, extra_neg, cfg, sketch_lora_strength, char_lora_strength)
ROUND1 = [
    ("baseline_7of10",
     "cozy warm soft lighting, masterpiece, best quality",
     "", 5.0, 0.0, 0.85),
    ("soft_flat",
     "flat color, pastel colors, muted palette, soft lighting",
     "shiny skin, glossy, high contrast, hdr", 4.5, 0.0, 0.85),
    ("sketch_soft",
     "flat color, pastel colors, sketch, loose lineart, hand-drawn",
     "shiny skin, glossy, high contrast, hdr, detailed shading", 4.5, 0.4, 0.85),
    ("watercolor_soft",
     "watercolor (medium), soft shading, pastel colors, gentle lineart",
     "shiny skin, glossy, high contrast, hdr", 4.5, 0.0, 0.85),
    ("effortless_low_cfg",
     "flat color, pastel colors, sketch, simple shading, limited palette",
     "shiny skin, glossy, high contrast, hdr, intricate, ornate", 3.8, 0.3, 0.85),
    ("doujin_soft",
     "flat color, pastel colors, painterly, soft focus, hand-drawn, "
     "rough sketch lines, cozy atmosphere",
     "shiny skin, glossy, hdr, high contrast, 3d, render", 4.2, 0.5, 0.85),
]

# Round 2 — owner picked effortless_low_cfg-left at 7.8/10 ("a bit wrong angle,
# doesn't feel that great yet"). Push toward the references: airier (white space,
# pale washed palette), thinner delicate lines, near-zero shading, and loosen the
# character LoRA's baked indie-VN pen. Two webcam angles.
_AIRY = (
    "flat color, pale color palette, pastel colors, white background, "
    "minimal shading, light blush, thin delicate lineart, sketch, simple shading"
)
_AIRY_NEG = "shiny skin, glossy, high contrast, hdr, intricate, ornate, detailed shading, 3d, render"
ROUND2 = [
    ("airy_anchor",
     _AIRY + ", three-quarter view",
     _AIRY_NEG, 3.8, 0.35, 0.85),
    ("airy_loose_pen",
     _AIRY + ", doodle, casual hand-drawn sketch, three-quarter view",
     _AIRY_NEG, 3.6, 0.55, 0.7),
    ("airy_webcam_angle",
     _AIRY + ", from above, slight high angle, looking up at viewer",
     _AIRY_NEG, 3.8, 0.4, 0.75),
]

# Round 3 — owner: airy pen ≈ right (8.2), but ANGLE must be frontal webcam
# (her facing the camera straight-on, keyboard at the bottom edge of frame —
# like r2's loose_pen right) and the pose was "insanely lifeless" → life comes
# from GESTURE. Pen locked to the airy blend; four life poses.
_R3_PEN_POS = (
    "flat color, pale color palette, pastel colors, white background, "
    "minimal shading, light blush, thin delicate lineart, sketch, "
    "facing viewer, straight-on, webcam point of view, "
    "computer keyboard in foreground at bottom edge of frame"
)
_R3_SK, _R3_CHAR, _R3_CFG = 0.5, 0.72, 3.8
ROUND3 = [
    ("life_chin_rest",
     _R3_PEN_POS + ", head tilted, chin resting on one hand, elbow on desk, "
     "soft knowing cat-like smile, half-lidded eyes looking at viewer",
     _AIRY_NEG, _R3_CFG, _R3_SK, _R3_CHAR),
    ("life_typing_glance",
     _R3_PEN_POS + ", hands on keyboard mid-typing, glancing up at viewer, "
     "playful smirk, one fox ear perked up",
     _AIRY_NEG, _R3_CFG, _R3_SK, _R3_CHAR),
    ("life_mug_sip",
     _R3_PEN_POS + ", holding a steaming mug with both sleeves, sweater paws, "
     "peeking at viewer over the mug, relaxed sleepy eyes",
     _AIRY_NEG, _R3_CFG, _R3_SK, _R3_CHAR),
    ("life_stretch",
     _R3_PEN_POS + ", leaning back stretching arms above head, eyes closed, "
     "sleepy content smile, messy hair strand",
     _AIRY_NEG, _R3_CFG, _R3_SK, _R3_CHAR),
]

# Round 4 — owner triangulated on r3 chin_rest: LEFT = right model/wrong angle,
# RIGHT = right angle/chibi-drift model. Same recipe, different seeds → the
# difference was luck. Kill the luck: framing forced to the webcam spec
# (keyboard bottom edge, frontal, close) + hard proportion guards. One recipe,
# many seeds — owner picks the intersection.
ROUND4 = [
    ("webcam_lock",
     _AIRY + ", facing viewer, straight-on, close to camera, upper body, "
     "head and shoulders large in frame, computer keyboard at the very bottom "
     "edge of the frame in the foreground, desk edge visible, "
     "head tilted, chin resting on one hand, elbow on desk, "
     "soft knowing cat-like smile, half-lidded crimson red eyes looking at viewer, "
     "mature adult proportions, slender adult woman",
     _AIRY_NEG + ", chibi, deformed, big head, tiny body, child, loli",
     3.8, 0.5, 0.72),
]

RECIPES = ROUND1
SEEDS = [51001, 51002]


def get(p, timeout=20):
    return urllib.request.urlopen(BASE + p, timeout=timeout).read()


def post(p, o):
    r = urllib.request.Request(BASE + p, data=json.dumps(o).encode(),
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read())


def wait_server(max_s=240):
    t0 = time.time()
    while time.time() - t0 < max_s:
        try:
            get("/system_stats")
            return True
        except Exception:
            time.sleep(3)
    return False


def gen(label, pos, neg, cfg, sketch_str, seed, char_str=0.85, steps=26):
    wf = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "10": {"class_type": "LoraLoader", "inputs": {
            "model": ["4", 0], "clip": ["4", 1], "lora_name": CHAR_LORA,
            "strength_model": char_str, "strength_clip": char_str}},
    }
    model_src, clip_src = "10", "10"
    if sketch_str > 0:
        wf["11"] = {"class_type": "LoraLoader", "inputs": {
            "model": ["10", 0], "clip": ["10", 1], "lora_name": SKETCH_LORA,
            "strength_model": sketch_str, "strength_clip": sketch_str}}
        model_src, clip_src = "11", "11"
    wf.update({
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": [clip_src, 1], "text": pos}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": [clip_src, 1], "text": neg}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler_ancestral",
            "scheduler": "normal", "denoise": 1.0, "model": [model_src, 0],
            "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": f"wsp_{label}", "images": ["8", 0]}},
    })
    pid = post("/prompt", {"prompt": wf})["prompt_id"]
    for _ in range(400):
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
    os.makedirs(PREV, exist_ok=True)
    if not wait_server():
        print("FATAL: ComfyUI not reachable", flush=True)
        return
    results = []
    for label, extra_pos, extra_neg, cfg, sk, char_str in RECIPES:
        pos = f"{_POSE}, {extra_pos}"
        neg = f"{_NEG_COMMON}, {extra_neg}" if extra_neg else _NEG_COMMON
        for seed in SEEDS:
            t0 = time.time()
            r = gen(label, pos, neg, cfg, sk, seed, char_str)
            if not r:
                print(f"FAIL {label} seed {seed}", flush=True)
                continue
            im = Image.open(r).convert("RGB")
            im.save(os.path.join(PREV, f"{label}_{seed}.png"))
            results.append((label, seed, im))
            print(f"OK {label} seed {seed}  {time.time()-t0:5.1f}s", flush=True)

    cols = len(SEEDS)
    rows = len(RECIPES)
    cw, chh = 340, 340
    sheet = Image.new("RGB", (cols * cw + 220, rows * chh + 10), (18, 16, 20))
    dr = ImageDraw.Draw(sheet)
    for r_i, (label, *_rest) in enumerate(RECIPES):
        dr.text((8, r_i * chh + chh // 2), label, fill=(235, 210, 160))
        row_imgs = [im for (lbl, _, im) in results if lbl == label]
        for c_i, im in enumerate(row_imgs[:cols]):
            t = im.copy()
            t.thumbnail((cw - 6, chh - 6))
            sheet.paste(t, (220 + c_i * cw + 3, r_i * chh + 5))
    out = os.path.join(PREV, f"style_probe_sheet_{_round_name}.jpg")
    sheet.save(out, quality=90)
    print(f"SHEET -> {out}", flush=True)
    print("STYLE PROBE DONE", flush=True)


if __name__ == "__main__":
    import sys

    _round_name = sys.argv[1] if len(sys.argv) > 1 else "r1"
    if _round_name == "r2":
        RECIPES = ROUND2
        SEEDS = [51001, 51002, 51003]
    elif _round_name == "r3":
        RECIPES = ROUND3
        SEEDS = [52001, 52002]
    elif _round_name == "r4":
        RECIPES = ROUND4
        SEEDS = [53001, 53002, 53003, 53004, 53005, 53006]
    main()
