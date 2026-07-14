"""Shared helpers for pod battery stages: VRAM snapshots + result files."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

RESULTS = Path("/workspace/results")


def vram_mib() -> int:
    """Total VRAM in use on GPU 0 per nvidia-smi (whole card, all processes)."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True, timeout=10)
        return int(out.strip().splitlines()[0])
    except Exception:
        return -1


def gpu_name() -> str:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            text=True, timeout=10)
        return out.strip().splitlines()[0]
    except Exception:
        return "unknown"


def write_result(stage: str, data: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    data = {"stage": stage, "gpu": gpu_name(),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"), **data}
    path = RESULTS / f"{stage}.json"
    path.write_text(json.dumps(data, indent=2))
    print(f"[{stage}] result -> {path}")
    print(json.dumps(data, indent=2))


class Timer:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.s = time.perf_counter() - self.t0


SPEECH_WAV = "/workspace/sample_speech.wav"  # jfk.flac from openai/whisper, 16k mono


def load_speech_sample():
    """16kHz mono float32 speech for whisper stages (datasets' audio decode now
    requires torchcodec; a plain wav sidesteps it entirely)."""
    import soundfile as sf

    data, _sr = sf.read(SPEECH_WAV, dtype="float32")
    return data
