"""Batch painterly restyle of all koroki_* expression sprites via FLUX+SketchPad img2img,
at BOTH denoise 0.35 and 0.65 (user verdict from the sweep: 0.35 keeps face structure,
0.65 is a different-but-interesting style; 0.55 was rejected). Same seed per expression
across levels for a fair comparison.

Pipeline per sprite: composite on cream -> img2img -> white-key cut (NO bbox crop — the
expression set must stay canvas-aligned for the puppet face-swap) -> comparison sheet
composited over P_bedroom_night so the choice is judged in the real room context.

Outputs:
  data/art_previews/restyle_batch/raw_{035,065}/koroki_<expr>.png   (uncut)
  data/art_previews/restyle_batch/cut_{035,065}/koroki_<expr>.png   (transparent, aligned)
  data/art_previews/restyle_batch_sheet.jpg                          (the decision sheet)
"""
import json
import os
import time
import urllib.request

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

BASE = "http://127.0.0.1:8188"
ROOT = r"C:/Users/Shinn/Desktop/Koroki"
OUT = os.path.join(ROOT, "tools", "ComfyUI", "output")
INP = os.path.join(ROOT, "tools", "ComfyUI", "input")
SPR = os.path.join(ROOT, "assets", "koroki_sprites")
PREV = os.path.join(ROOT, "data", "art_previews")
BATCH = os.path.join(PREV, "restyle_batch")
ROOM = os.path.join(ROOT, "assets", "flux_style_farm", "P_bedroom_night.png")
CREAM = (244, 240, 232)
LORA = "sketch_sketchpad_concept.safetensors"

EXPRS = {
    "neutral":   "half-lidded crimson-red droopy eyes, soft neutral expression",
    "happy":     "crimson-red eyes, bright happy smile, cheerful",
    "smug":      "half-lidded crimson-red eyes, smug teasing grin",
    "sleepy":    "crimson-red eyes nearly closed, sleepy drowsy expression",
    "surprised": "wide open crimson-red eyes, surprised parted lips",
    "pout":      "half-lidded crimson-red droopy eyes, pouting puffed cheeks",
}
PROMPT = (
    "a cute girl with grey fox ears with cream inner fur, long wavy ash-grey hair, {expr}, "
    "small red x-shaped hairpins and a red broken-heart hairpin on her bangs, wearing an oversized "
    "cream knit sweater, kneeling, cozy warm soft lighting, digrngbrsh, loose painterly "
    "concept-art illustration, hand-painted, plain soft background"
)
LEVELS = [(0.35, "035"), (0.65, "065")]


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


def gen(src_name, expr_text, denoise, seed, steps=28, guid=3.5):
    wf = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "flux1-dev-fp8.safetensors"}},
        "20": {"class_type": "LoadImage", "inputs": {"image": src_name}},
        "22": {"class_type": "VAEEncode", "inputs": {"pixels": ["20", 0], "vae": ["4", 2]}},
        "10": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["4", 0], "lora_name": LORA, "strength_model": 1.0}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": PROMPT.format(expr=expr_text)}},
        "26": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["6", 0], "guidance": guid}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": ""}},
        "3": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": 1.0,
              "sampler_name": "euler", "scheduler": "simple", "denoise": denoise,
              "model": ["10", 0], "positive": ["26", 0], "negative": ["7", 0], "latent_image": ["22", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "krbatch", "images": ["8", 0]}},
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
        time.sleep(3)
    return None


def alpha_transfer_cut(restyled, original, dilate_px=4, bg_dist=20, feather=1.2):
    """Character cut via ALPHA TRANSFER, not border flood-fill.

    Border flood fails on characters: the body/shadow touch the frame edge and wall off
    interior background pockets (between legs, hair gaps), and grid-seeding the interior
    would eat her cream sweater (bright + low-sat, same as the background). Instead we
    exploit what img2img guarantees — geometry stays put: start from the ORIGINAL sprite's
    alpha, dilate a few px for painterly silhouette drift, and inside that band veto pixels
    close to the measured background color. Pockets resolve automatically, the sweater is
    deep inside the silhouette and untouchable, and FLUX's painted ground-shadow (outside
    the original alpha) is dropped. Also clamps every expression to a consistent silhouette,
    which is exactly what the aligned face-swap needs. NO bbox crop — canvas alignment.
    """
    rgb = restyled.convert("RGB")
    arr = np.asarray(rgb).astype(np.int16)
    h, w, _ = arr.shape

    oa = original.split()[-1].resize((w, h)) if original.size != (w, h) else original.split()[-1]
    oa_np = np.asarray(oa)
    solid = Image.fromarray(np.where(oa_np > 40, 255, 0).astype("uint8"), "L")
    cand = np.asarray(solid.filter(ImageFilter.MaxFilter(2 * dilate_px + 1))) > 0

    # measure bg color from the border ring, restricted to pixels outside the candidate
    ring = np.zeros((h, w), dtype=bool)
    m = 14
    ring[:m, :] = ring[-m:, :] = ring[:, :m] = ring[:, -m:] = True
    ring &= ~cand
    bg = np.median(arr[ring], axis=0) if ring.sum() > 100 else np.array([242, 238, 230])

    near_bg = (np.abs(arr - bg).max(axis=2) <= bg_dist)
    alpha = np.where(cand & ~near_bg, 255, 0).astype("uint8")
    # inside the ORIGINAL solid silhouette, always keep (protects bright sweater pixels)
    alpha[np.asarray(solid) > 0] = 255

    a = Image.fromarray(alpha, "L").filter(ImageFilter.MinFilter(3))
    if feather:
        a = a.filter(ImageFilter.GaussianBlur(feather))
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out


def load_originals():
    originals = {}
    for expr in EXPRS:
        originals[expr] = Image.open(os.path.join(SPR, f"koroki_{expr}.png")).convert("RGBA")
    return originals


def cut_and_save(expr, tag, originals):
    raw_path = os.path.join(BATCH, f"raw_{tag}", f"koroki_{expr}.png")
    if not os.path.exists(raw_path):
        return None
    im = Image.open(raw_path).convert("RGB")
    cut = alpha_transfer_cut(im, originals[expr])
    cut.save(os.path.join(BATCH, f"cut_{tag}", f"koroki_{expr}.png"))
    return cut


def build_sheet(originals, results, levels=None, sheet_name="restyle_batch_sheet.jpg"):
    """Decision sheet: rows = expressions, cols = original + one per level, over the hero bedroom."""
    levels = levels or LEVELS
    room = Image.open(ROOM).convert("RGB")
    cw, chh = 300, 460
    cols = [("original", None)] + [(f"denoise {dn}" if isinstance(dn, float) else str(dn), tag) for dn, tag in levels]
    sheet = Image.new("RGB", (len(cols) * cw, len(EXPRS) * (chh + 22) + 26), (18, 16, 20))
    dr = ImageDraw.Draw(sheet)
    # one fixed room crop as backdrop for every cell (center-left of the bedroom, floor area)
    crop = room.crop((150, 100, 150 + int(cw * (832 - 100) / chh), 832)).resize((cw, chh))
    for ci, (label, _) in enumerate(cols):
        dr.text((ci * cw + 8, 6), label, fill=(235, 210, 160))
    for ri, expr in enumerate(EXPRS):
        oy = 26 + ri * (chh + 22)
        for ci, (label, tag) in enumerate(cols):
            spr = originals[expr] if tag is None else results.get((expr, tag))
            cell = crop.copy()
            if spr is not None:
                t = spr.copy()
                t.thumbnail((cw, chh - 20))
                cell.paste(t, ((cw - t.width) // 2, chh - t.height), t)
            sheet.paste(cell, (ci * cw, oy))
        dr.text((8, oy + chh + 4), expr, fill=(235, 235, 235))
    out = os.path.join(PREV, sheet_name)
    sheet.save(out, quality=90)
    print(f"SHEET -> {out}", flush=True)


def main(recut_only=False, ash=False):
    # --ash: sources in ComfyUI input were already recolored ash-grey by
    # koroki_ashgrey_recolor.py apply — regen ONLY 0.35 into its own dirs and build a
    # sheet comparing original / brown 0.35 / ash 0.35.
    levels = [(0.35, "035_ash")] if ash else LEVELS
    for _, tag in levels:
        os.makedirs(os.path.join(BATCH, f"raw_{tag}"), exist_ok=True)
        os.makedirs(os.path.join(BATCH, f"cut_{tag}"), exist_ok=True)

    originals = load_originals()
    results = {}  # (expr, tag) -> cut RGBA

    if recut_only:
        for expr in EXPRS:
            for _, tag in LEVELS:
                cut = cut_and_save(expr, tag, originals)
                if cut is not None:
                    a = np.asarray(cut.split()[-1])
                    print(f"RECUT {expr:10s} {tag}  opaque {(a>10).mean()*100:4.1f}%", flush=True)
                    results[(expr, tag)] = cut
        build_sheet(originals, results)
        print("RECUT DONE", flush=True)
        return

    if not wait_server():
        print("FATAL: ComfyUI not reachable", flush=True)
        return

    # source prep: composite each sprite on cream (img2img needs full RGB).
    # In --ash mode the recolor script already wrote the (ash-grey) kb_*.png inputs.
    if not ash:
        for expr, src in originals.items():
            W, H = src.size
            cream = Image.new("RGBA", (W, H), CREAM + (255,))
            cream.alpha_composite(src)
            cream.convert("RGB").save(os.path.join(INP, f"kb_{expr}.png"))

    for i, (expr, expr_text) in enumerate(EXPRS.items()):
        for denoise, tag in levels:
            if not wait_server():
                print("FATAL: ComfyUI gone mid-batch", flush=True)
                return
            t0 = time.time()
            r = gen(f"kb_{expr}.png", expr_text, denoise, seed=7700 + i)
            if not r:
                print(f"FAIL {expr} @{denoise}", flush=True)
                continue
            im = Image.open(r).convert("RGB")
            im.save(os.path.join(BATCH, f"raw_{tag}", f"koroki_{expr}.png"))
            cut = cut_and_save(expr, tag, originals)
            a = np.asarray(cut.split()[-1])
            print(f"OK {expr:10s} @{denoise}  {time.time()-t0:5.1f}s  opaque {(a>10).mean()*100:4.1f}%", flush=True)
            results[(expr, tag)] = cut

    if ash:
        # pull the brown 0.35 cuts from disk for the comparison column
        for expr in EXPRS:
            p = os.path.join(BATCH, "cut_035", f"koroki_{expr}.png")
            if os.path.exists(p):
                results[(expr, "035")] = Image.open(p).convert("RGBA")
        build_sheet(
            originals, results,
            levels=[("0.35 brown", "035"), ("0.35 ash-grey", "035_ash")],
            sheet_name="restyle_ash_sheet.jpg",
        )
    else:
        build_sheet(originals, results)
    print("RESTYLE BATCH DONE", flush=True)


if __name__ == "__main__":
    import sys
    main(recut_only="--recut" in sys.argv, ash="--ash" in sys.argv)
