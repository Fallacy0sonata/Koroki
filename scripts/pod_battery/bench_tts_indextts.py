"""IndexTTS2 RTF on 24GB-class — the production-voice-on-3090 plan.

Run with the tts venv:  /workspace/venv_tts/bin/python bench_tts_indextts.py
API mirrors experiments/index-tts/adapter.py at home (infer_v2.IndexTTS2).
Reference speaker: an example wav shipped in the index-tts repo (public, not hers).
"""
from __future__ import annotations

import glob
import time
from pathlib import Path

from util import vram_mib, write_result

MODEL_DIR = "/workspace/models/IndexTTS-2"
TEXT = ("Honestly, today went better than I expected — the tests passed, "
        "the weather held up, and I even had time for a proper meal.")


def find_reference_wav() -> str | None:
    # index-tts ships no audio; the JFK sample (setup downloads it) is the reference
    for pat in ("/workspace/sample_speech.wav",
                "/workspace/index-tts/examples/*.wav",
                "/workspace/index-tts/**/*.wav"):
        hits = glob.glob(pat, recursive=True)
        if hits:
            return hits[0]
    return None


def main() -> None:
    res: dict = {"model_dir": MODEL_DIR, "error": None}
    base_vram = vram_mib()
    try:
        import soundfile as sf
        from indextts.infer_v2 import IndexTTS2

        ref = find_reference_wav()
        if ref is None:
            raise RuntimeError("no example wav found in index-tts repo")
        res["reference"] = ref

        cfg = str(Path(MODEL_DIR) / "config.yaml")
        t0 = time.perf_counter()
        try:
            tts = IndexTTS2(cfg_path=cfg, model_dir=MODEL_DIR, use_fp16=True,
                            device="cuda", use_accel=True)
        except Exception:
            tts = IndexTTS2(cfg_path=cfg, model_dir=MODEL_DIR, use_fp16=True,
                            device="cuda", use_accel=False)
            res["accel"] = False
        res["load_s"] = round(time.perf_counter() - t0, 1)
        res["vram_loaded_mib"] = vram_mib() - max(base_vram, 0)

        runs = []
        for i in range(3):
            out = f"/workspace/results/indextts_bench_{i}.wav"
            t0 = time.perf_counter()
            tts.infer(ref, TEXT, out, verbose=False)
            dt = time.perf_counter() - t0
            data, sr = sf.read(out)
            dur = len(data) / sr
            runs.append({"synth_s": round(dt, 2), "audio_s": round(dur, 2),
                         "rtf": round(dt / dur, 2)})
        res["warmup"] = runs[0]
        res["runs"] = runs[1:]
        res["rtf_avg"] = round(sum(r["rtf"] for r in runs[1:]) / 2, 2)
        res["vram_peak_mib"] = vram_mib() - max(base_vram, 0)
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {exc}"
    write_result("tts_indextts2", res)


if __name__ == "__main__":
    main()
