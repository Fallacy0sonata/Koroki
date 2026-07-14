"""GPU whisper large-v3-turbo — the 3090 ears plan. Claimed: 1.6GB, ~0.2s/phrase."""
from __future__ import annotations

import time

from util import load_speech_sample, vram_mib, write_result


def main() -> None:
    res: dict = {"model": "large-v3-turbo", "error": None}
    base_vram = vram_mib()
    try:
        from faster_whisper import WhisperModel

        audio = load_speech_sample()
        res["sample_s"] = round(len(audio) / 16000, 1)

        t0 = time.perf_counter()
        model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
        res["load_s"] = round(time.perf_counter() - t0, 1)
        res["vram_loaded_mib"] = vram_mib() - max(base_vram, 0)

        times = []
        for i in range(4):
            t0 = time.perf_counter()
            segments, _ = model.transcribe(audio, language="en", beam_size=1)
            text = " ".join(s.text for s in segments)  # generator — drain it
            times.append(round(time.perf_counter() - t0, 2))
        res["warmup_s"] = times[0]
        res["transcribe_s"] = times[1:]
        res["text"] = text[:80]
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {exc}"
    write_result("whisper_turbo", res)


if __name__ == "__main__":
    main()
