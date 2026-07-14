"""Photon eyes (moondream 1.3.0 / kestrel engine) — the OPT-O3 recipe on 24GB.

Local vetting on the 4070 Ti: query 0.25s, point 0.04s, ~4.2GB fp16 floor.
Note: kestrel phones home usage rollups (never images) — fine on a throwaway pod.
"""
from __future__ import annotations

import time

from util import vram_mib, write_result


def make_test_image():
    """Synthetic game-UI-ish frame: labeled numbers + a button to point at."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1280, 720), (24, 26, 32))
    d = ImageDraw.Draw(img)
    d.text((60, 60), "Paperclips: 5,213", fill=(220, 220, 220))
    d.text((60, 120), "Funds: $41.50", fill=(220, 220, 220))
    d.rectangle([540, 320, 740, 380], fill=(70, 90, 200))
    d.text((575, 340), "Make Paperclip", fill=(255, 255, 255))
    return img


def main() -> None:
    res: dict = {"pkg": "moondream==1.3.0", "error": None}
    base_vram = vram_mib()
    try:
        import moondream as md

        img = make_test_image()
        t0 = time.perf_counter()
        model = md.vl(local=True, model="moondream2")
        res["load_s"] = round(time.perf_counter() - t0, 1)
        res["vram_loaded_mib"] = vram_mib() - max(base_vram, 0)

        q_times, answers = [], []
        for _ in range(3):
            t0 = time.perf_counter()
            ans = model.query(img, "How many paperclips does the counter show?")
            q_times.append(round(time.perf_counter() - t0, 2))
            answers.append(str(ans.get("answer", ans))[:60])
        res["query_s"] = q_times
        res["query_answer"] = answers[-1]

        t0 = time.perf_counter()
        pt = model.point(img, "the Make Paperclip button")
        res["point_s"] = round(time.perf_counter() - t0, 3)
        res["point_result"] = str(pt)[:120]
        res["vram_peak_mib"] = vram_mib() - max(base_vram, 0)
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {exc}"
    write_result("vision_photon", res)


if __name__ == "__main__":
    main()
