"""THE fit test — the whole 3090 stack resident and WORKING at once.

Spawns one process per organ (mirrors home's process-per-service architecture):
  llm     8B EXL2, generating in a loop          (~6.2GB expected)
  whisper large-v3-turbo, transcribing in a loop (~1.6-2GB)
  vision  Photon fp16, querying in a loop        (~4.2GB)
  tts     IndexTTS2, synthesizing in a loop      (~5GB, via venv_tts)

Paper math ≈ 17.5GB on 24GB. Deliverables: does it all fit, total VRAM under
load, and per-organ throughput WITH neighbors vs the solo numbers from the
individual stages (contention cost).

Run:  python costack.py            (parent — spawns roles, snapshots, reaps)
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from util import vram_mib, write_result

STATUS_DIR = Path("/workspace/results/costack_status")
OBSERVE_S = 90


# ── roles (child processes) ──────────────────────────────────────────

def _status(role: str, **kw) -> None:
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    (STATUS_DIR / f"{role}.json").write_text(json.dumps(kw))


def _work_loop(role: str, fn) -> None:
    """Run fn() forever, tracking iterations/sec in the status file."""
    done = 0
    t0 = time.perf_counter()
    while True:
        try:
            fn()
            done += 1
            _status(role, state="working", iters=done,
                    iter_s=round((time.perf_counter() - t0) / done, 2))
        except Exception as exc:
            _status(role, state="error", error=f"{type(exc).__name__}: {exc}"[:300])
            time.sleep(5)


def role_llm() -> None:
    from huggingface_hub import snapshot_download
    from exllamav2 import ExLlamaV2, ExLlamaV2Cache, ExLlamaV2Config, ExLlamaV2Tokenizer
    from exllamav2.generator import ExLlamaV2DynamicGenerator
    d = snapshot_download("TheMelonGod/Qwen3-8B-exl2", revision="8hb-4.5bpw")
    config = ExLlamaV2Config(d)
    config.max_seq_len = 3072  # home's actual brain ctx (settings.yaml)
    model = ExLlamaV2(config)
    cache = ExLlamaV2Cache(model, lazy=True)
    model.load_autosplit(cache)
    tok = ExLlamaV2Tokenizer(config)
    gen = ExLlamaV2DynamicGenerator(model=model, cache=cache, tokenizer=tok,
                                    paged=False)
    _status("llm", state="ready")
    _work_loop("llm", lambda: gen.generate(
        prompt="Write two sentences about rain.", max_new_tokens=96, add_bos=True))


def role_whisper() -> None:
    from faster_whisper import WhisperModel
    from util import load_speech_sample
    audio = load_speech_sample()
    model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
    _status("whisper", state="ready")

    def work():
        segs, _ = model.transcribe(audio, language="en", beam_size=1)
        list(segs)
    _work_loop("whisper", work)


def role_vision() -> None:
    import moondream as md
    from bench_vision_photon import make_test_image
    img = make_test_image()
    # CAGED: kestrel default (max_batch_size=4, auto KV) grabs ~all free VRAM —
    # it OOM'd the co-stack live (2026-07-07). 2048 pages covers one 2048² frame.
    model = md.vl(local=True, model="moondream2", max_batch_size=1,
                  kv_cache_pages=1024)  # 2048 pages left only 2MB free — game
                                        # frames are 1280x720, well under this
    _status("vision", state="ready")
    _work_loop("vision", lambda: model.query(img, "What number is shown for funds?"))


def role_tts() -> None:
    from pathlib import Path as P
    from indextts.infer_v2 import IndexTTS2
    from bench_tts_indextts import MODEL_DIR, find_reference_wav
    ref = find_reference_wav()
    cfg = str(P(MODEL_DIR) / "config.yaml")
    try:
        tts = IndexTTS2(cfg_path=cfg, model_dir=MODEL_DIR, use_fp16=True,
                        device="cuda", use_accel=True)
    except Exception:
        tts = IndexTTS2(cfg_path=cfg, model_dir=MODEL_DIR, use_fp16=True,
                        device="cuda", use_accel=False)
    _status("tts", state="ready")
    _work_loop("tts", lambda: tts.infer(
        ref, "The rain finally stopped this afternoon.", "/tmp/costack_tts.wav",
        verbose=False))


# ── parent ───────────────────────────────────────────────────────────

ROLES = {
    "llm": [sys.executable, __file__, "--role", "llm"],
    "whisper": [sys.executable, __file__, "--role", "whisper"],
    "vision": [sys.executable, __file__, "--role", "vision"],
    "tts": ["/workspace/venv_tts311/bin/python", __file__, "--role", "tts"],
}


def parent() -> None:
    res: dict = {"observe_s": OBSERVE_S, "roles": {}, "error": None}
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    for f in STATUS_DIR.glob("*.json"):
        f.unlink()
    base_vram = vram_mib()
    procs = {}
    try:
        # SEQUENTIAL loads, vision LAST — kestrel sizes its KV to free VRAM at
        # load time, so it must see the real leftovers (and it's caged anyway).
        # Also mirrors home's boot order.
        for name in ("llm", "tts", "whisper", "vision"):
            cmd = ROLES[name]
            procs[name] = subprocess.Popen(cmd, cwd=Path(__file__).parent)
            print(f"spawned {name} (pid {procs[name].pid})")
            deadline = time.time() + 8 * 60
            while time.time() < deadline:
                f = STATUS_DIR / f"{name}.json"
                state = json.loads(f.read_text())["state"] if f.exists() else "loading"
                if procs[name].poll() is not None and state == "loading":
                    state = "died"  # crashed before reporting
                    _status(name, state="died")
                print(f"  {name}: {state} | vram {vram_mib()} MiB")
                if state in ("ready", "working", "error", "died"):
                    break
                time.sleep(10)

        res["vram_all_loaded_mib"] = vram_mib()
        res["vram_baseline_mib"] = base_vram

        # observe under simultaneous load
        peak = 0
        t_end = time.time() + OBSERVE_S
        while time.time() < t_end:
            peak = max(peak, vram_mib())
            time.sleep(3)
        res["vram_peak_under_load_mib"] = peak

        for name in ROLES:
            f = STATUS_DIR / f"{name}.json"
            res["roles"][name] = json.loads(f.read_text()) if f.exists() else {"state": "never reported"}
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        for name, p in procs.items():
            p.terminate()
    write_result("costack", res)


if __name__ == "__main__":
    if "--role" in sys.argv:
        role = sys.argv[sys.argv.index("--role") + 1]
        {"llm": role_llm, "whisper": role_whisper,
         "vision": role_vision, "tts": role_tts}[role]()
    else:
        parent()
