"""CosyVoice2 HTTP adapter for Koroki — production TTS (port 9004).

Speaks the EXACT IndexTTS adapter contract (/synthesize, /health, /ready,
/version, /unload, /load) so the orchestrator can't tell engines apart —
switching engines = changing services.tts.adapter_url in settings.yaml.

Built from the 2026-07-04 bench findings (LEGACY + master_queue "OWNER'S GAMBIT"):
- instruct2-only path; instruct text ALWAYS ends with <|endofprompt|> —
  without the delimiter the model READS THE INSTRUCTION ALOUD.
- Prompt is a FILE PATH (this repo revision's frontend loads/resamples itself).
- Emotion bridge uses ACOUSTIC wording, not semantic labels ("speak very slowly,
  quietly, dragging" — not "tired"; owner round-3: labels ≈ +20% effect only),
  plus the inference speed parameter for tired/sad.
- Guards for the owner-heard artifact classes:
  (a) duration-sanity reseed-retry — catches runaway/garbage tails (the JP line
      that spoke 5 s then recorded mic-fumbling was 3.5x expected duration);
  (b) energy tail-trim — cuts trailing non-speech garbage that survives retry;
  (c) peak normalize — tames the occasional "unreasonably loud" word.

Runs in .venv_cosyvoice (Python 3.11). Start: scripts/easy_start_cosyvoice_adapter.ps1
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import random
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("cosyvoice.adapter")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CV_REPO = _REPO_ROOT / "experiments" / "cosyvoice" / "CosyVoice"
sys.path.insert(0, str(_CV_REPO))
sys.path.insert(0, str(_CV_REPO / "third_party" / "Matcha-TTS"))

_VOICE_SAMPLE = "voice_samples/EN_sample.wav"
_MODEL_DIR = _REPO_ROOT / "experiments" / "cosyvoice" / "pretrained_models" / "CosyVoice2-0.5B"

_tts = None
_synth_lock = asyncio.Lock()


# ── contract (mirror of experiments/index-tts/adapter.py) ────────────


class SynthesizeRequest(BaseModel):
    request_id: str = Field(default="", max_length=128)
    text: str = Field(..., min_length=1, max_length=4000)
    relationship_score: Annotated[int, Field(ge=0, le=100)] = 50
    emotion: str = Field(default="neutral", max_length=64)
    emotion_intensity: Annotated[int, Field(ge=0, le=100)] = 50
    emotion_variant: str | None = Field(default=None, max_length=64)
    audio_prompt: str | None = None
    # IndexTTS2 blend order: [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]
    emo_vector: list[float] | None = None


def _resolve_voice_sample(audio_prompt: str | None) -> str:
    if audio_prompt:
        return audio_prompt
    koroki_root = Path(os.environ.get("KOROKI_ROOT", str(_REPO_ROOT)))
    candidate = koroki_root / _VOICE_SAMPLE
    if candidate.exists():
        return str(candidate)
    for wav in sorted((koroki_root / "voice_samples").glob("*.wav")):
        logger.warning("EN_sample.wav missing, using: %s", wav)
        return str(wav)
    raise FileNotFoundError("No voice sample found in voice_samples/")


# ── emotion bridge: her affect → acoustic instruct + speed ───────────

# (base instruction, strong instruction, speed) per emotion bucket.
# Acoustic wording per owner round-3 verdict: describe HOW to speak, not a label.
_EMOTION_MAP: dict[str, tuple[str, str, float]] = {
    "happy": (
        "Speak with a bright, smiling voice, light and upbeat.",
        "Speak with strong excitement, high energy, big smile in the voice, almost laughing.",
        1.0,
    ),
    "excited": (
        "Speak with rising energy and a quick, lively rhythm.",
        "Speak very fast and energetic, thrilled, voice jumping with excitement.",
        1.05,
    ),
    "sad": (
        "Speak quietly and slowly in a low, soft voice with small pauses.",
        "Speak very quietly and slowly, voice heavy and low, on the verge of tears.",
        0.92,
    ),
    "melancholic": (
        "Speak softly and slowly with a wistful, distant tone.",
        "Speak very softly, slow and distant, each word reluctant and heavy.",
        0.92,
    ),
    "tired": (
        "Speak slowly and quietly, in a low, flat voice, dragging the words slightly.",
        "Speak extremely slowly and quietly, voice low and flat, dragging every word, "
        "with audible sleepy breaths between phrases.",
        0.82,
    ),
    "calm": (
        "Speak naturally in a calm, relaxed, unhurried voice.",
        "Speak in a deeply calm, slow, soothing voice, almost a murmur.",
        0.96,
    ),
    "teasing": (
        "Speak with a sly, sing-song rhythm, drawing out some words with a smirk in the voice.",
        "Speak with a heavy sing-song, mocking rhythm, dragging words out playfully, "
        "smug and gloating like teasing a close friend.",
        1.0,
    ),
    "playful": (
        "Speak lightly with a bouncy, mischievous rhythm.",
        "Speak with a very bouncy, mischievous, grinning energy, quick and cheeky.",
        1.02,
    ),
    "angry": (
        "Speak sharply with a hard, clipped tone.",
        "Speak fast and sharp, hard consonants, voice tight with irritation.",
        1.05,
    ),
    "annoyed": (
        "Speak flatly with a clipped, unimpressed tone and small sighs.",
        "Speak with heavy exasperation, flat and clipped, sighing between phrases.",
        0.98,
    ),
    "afraid": (
        "Speak quietly and quickly with a hesitant, unsteady voice.",
        "Speak in a shaky, breathless, hesitant voice, words tumbling out unevenly.",
        1.02,
    ),
    "surprised": (
        "Speak with sudden rising energy and a lifted, quick tone.",
        "Speak with a burst of energy, pitch jumping up, genuinely startled.",
        1.05,
    ),
    "warm": (
        "Speak softly and warmly, gentle and close.",
        "Speak very softly and warmly, intimate and gentle, slightly slower, close to the mic.",
        0.95,
    ),
    "neutral": (
        "Speak naturally in a calm, casual voice.",
        "Speak naturally in a calm, casual voice.",
        1.0,
    ),
}

_ALIASES = {
    "content": "calm", "cozy": "warm", "affectionate": "warm", "caring": "warm",
    "tender": "warm", "sleepy": "tired", "drained": "tired", "exhausted": "tired",
    "joyful": "happy", "cheerful": "happy", "smug": "teasing", "mischievous": "playful",
    "anxious": "afraid", "nervous": "afraid", "curious": "playful", "lonely": "melancholic",
    "disgusted": "annoyed", "frustrated": "annoyed", "irritated": "annoyed",
}

# emo_vector dims (IndexTTS order) → buckets, used when the label is unknown.
_VECTOR_BUCKETS = ["happy", "angry", "sad", "afraid", "annoyed", "melancholic", "surprised", "calm"]


def build_instruct(emotion: str, intensity: int, emo_vector: list[float] | None) -> tuple[str, float]:
    """Map her affect state to (instruct_text_with_delimiter, speed)."""
    key = (emotion or "neutral").strip().lower()
    key = _ALIASES.get(key, key)
    if key not in _EMOTION_MAP and emo_vector and len(emo_vector) == 8:
        dom = int(np.argmax(emo_vector))
        if emo_vector[dom] >= 0.2:
            key = _VECTOR_BUCKETS[dom]
    base, strong, speed = _EMOTION_MAP.get(key, _EMOTION_MAP["neutral"])
    instruct = strong if intensity >= 60 else base
    if intensity >= 60 and key != "neutral":
        # strong tier already exaggerated; extreme intensity slows/speeds a touch more
        speed = speed + (speed - 1.0) * 0.5
    return instruct + "<|endofprompt|>", round(max(0.75, min(1.15, speed)), 3)


# ── guards ───────────────────────────────────────────────────────────


def _expected_duration(text: str) -> float:
    """Rough speech-duration estimate for the duration-sanity guard."""
    cjk = sum(1 for ch in text if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿")
    latin = max(0, len(text) - cjk)
    return max(1.0, 0.075 * latin + 0.18 * cjk + 0.5)


def _trim_tail_garbage(wav: np.ndarray, sr: int) -> np.ndarray:
    """Cut trailing non-speech (the 'mic fumbling' tail class).

    Finds the last frame whose RMS is within 30 dB of the loudest frame and
    keeps a 300 ms hangover after it. Only trims if it removes > 0.4 s so
    normal endings are never touched.
    """
    frame = int(0.025 * sr)
    if len(wav) < frame * 8:
        return wav
    n = len(wav) // frame
    rms = np.sqrt(np.mean(wav[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12)
    # -38 dB rel + 0.6 s minimum: her soft trailing endings must NEVER be
    # mistaken for garbage — this trim only exists for gross non-speech tails.
    threshold = rms.max() * 10 ** (-38 / 20)
    loud = np.nonzero(rms > threshold)[0]
    if len(loud) == 0:
        return wav
    end = min(len(wav), (loud[-1] + 1) * frame + int(0.3 * sr))
    if len(wav) - end > int(0.6 * sr):
        logger.info("tail trim: %.2fs -> %.2fs", len(wav) / sr, end / sr)
        return wav[:end]
    return wav


def _peak_guard(wav: np.ndarray) -> np.ndarray:
    peak = float(np.abs(wav).max() or 0.0)
    if peak > 0.98:
        wav = wav * (0.95 / peak)
    return wav


def _glitch_score(wav: np.ndarray, sr: int) -> int:
    """Max per-100ms count of violent sample-to-sample jumps.

    Broken 'mic damage' audio shows bursts of 300-1000 jumps per 100 ms;
    clean speech stays well under ~200 (measured on owner-flagged sample vs
    known-good, 2026-07-04). Used to reseed-retry glitchy generations.
    """
    diff = np.abs(np.diff(wav))
    bucket = max(1, int(0.1 * sr))
    n = len(diff) // bucket
    if n == 0:
        return 0
    counts = (diff[: n * bucket].reshape(n, bucket) > 0.25).sum(axis=1)
    return int(counts.max())


_GLITCH_THRESHOLD = 300  # per-100ms jump count that marks a broken generation


# ── synthesis ────────────────────────────────────────────────────────


def _synth_once(text: str, instruct: str, prompt_path: str, speed: float, seed: int) -> np.ndarray:
    import torch
    from cosyvoice.utils.common import set_all_random_seed

    set_all_random_seed(seed)
    chunks = []
    try:
        gen = _tts.inference_instruct2(text, instruct, prompt_path, stream=False, speed=speed)
    except TypeError:  # older signature without speed
        gen = _tts.inference_instruct2(text, instruct, prompt_path, stream=False)
    for out in gen:
        chunks.append(out["tts_speech"])
    return torch.cat(chunks, dim=1).squeeze(0).float().cpu().numpy()


def _normalize_text(text: str) -> str:
    import re
    text = text.strip()
    # Tildes destabilize speech-token generation — owner heard "voice breaking
    # like a broken mic" on "ohi~ genuine yeah?" (glitch bursts confirmed by
    # discontinuity analysis, 2026-07-04). Her text keeps the ~; TTS never sees it.
    text = text.replace("~", "")
    text = re.sub(r"[.]{2,}\s*$", ".", text)
    text = re.sub(r"[…]\s*$", ".", text)
    text = re.sub(r"[.]{2,}", ", ", text)
    text = re.sub(r"[…]", ", ", text)
    text = re.sub(r"^[\s,;:\-]+", "", text)
    text = text.strip()
    # CosyVoice stops generating early on unterminated text — and dropping the
    # final period is her signature style ("small stuff though"), so this fired
    # constantly (owner heard "dramat—" mid-word cuts, 2026-07-04). Always end
    # with terminal punctuation before synthesis.
    if text and text[-1] not in ".!?。！？":
        text += "."
    # CosyVoice's frontend splits on sentence boundaries and DROPS tiny trailing
    # segments outright ("…what it says here. why." spoke everything except
    # "why" — the log showed only one synthesized segment). Merge a short final
    # sentence into the previous one so it can never stand alone.
    m = re.search(r"^(.*[.!?])\s*([^.!?]{1,12}[.!?])$", text, re.S)
    if m and len(m.group(2)) <= 13:
        text = m.group(1)[:-1] + ", " + m.group(2)
    return text


def _run_synthesis(text: str, instruct: str, prompt_path: str, speed: float, label: str) -> tuple[np.ndarray, int]:
    """Blocking synthesis with the duration-sanity retry guard.

    Two failure modes, both duration-detectable:
    - OVERSHOOT (> 2.4x expected): runaway/garbage tail — the instruments class.
    - UNDERSHOOT (< 0.55x expected): early stop — dropped final words
      ("...nothing dramat—"). Reseed and retry both; keep the best attempt.
    """
    text = _normalize_text(text)
    sr = _tts.sample_rate
    expected = _expected_duration(text) / max(speed, 0.5)
    lo, hi = 0.55 * expected, max(2.0, 2.4 * expected)
    best: np.ndarray | None = None
    best_score = -1.0
    for attempt in range(3):
        seed = random.randint(0, 2**31 - 1)
        wav = _synth_once(text, instruct, prompt_path, speed, seed)
        dur = len(wav) / sr
        glitch = _glitch_score(wav, sr)
        if lo <= dur <= hi and glitch < _GLITCH_THRESHOLD:
            best = wav
            break
        # Score fallbacks: duration closeness minus a glitch penalty.
        score = -abs(float(np.log((dur + 0.1) / expected))) - (glitch / 1000.0)
        if score > best_score:
            best, best_score = wav, score
        logger.warning("[%s] guard: %.1fs vs expected %.1fs [%.1f-%.1f], glitch=%d "
                       "(attempt %d) — reseeding", label, dur, expected, lo, hi,
                       glitch, attempt + 1)
    wav = _trim_tail_garbage(best, sr)
    wav = _peak_guard(wav)
    return wav, sr


_WARMUP_TEXT = (
    "Warming up the voice for the day, one full sentence with enough words in it "
    "that the generator settles into a natural rhythm."
)


# ── app ──────────────────────────────────────────────────────────────


async def _startup():
    global _tts
    try:
        from cosyvoice.cli.cosyvoice import CosyVoice2
    except Exception:
        logger.exception("Failed to import CosyVoice2")
        return
    if not _MODEL_DIR.exists():
        logger.error("Model dir missing: %s — run scripts/setup_cosyvoice.ps1", _MODEL_DIR)
        return
    try:
        t0 = time.time()
        _tts = CosyVoice2(str(_MODEL_DIR), load_jit=False, load_trt=False, fp16=True)
        logger.info("CosyVoice2 ready in %.1fs (sr=%d)", time.time() - t0, _tts.sample_rate)
    except Exception:
        logger.exception("Failed to initialize CosyVoice2")
        return
    try:
        prompt = _resolve_voice_sample(None)
        instruct, speed = build_instruct("neutral", 50, None)
        await asyncio.to_thread(_run_synthesis, _WARMUP_TEXT, instruct, prompt, speed, "warmup")
        logger.info("Warm-up complete")
    except Exception:
        logger.exception("Warm-up failed (non-fatal)")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _startup()
    yield


app = FastAPI(title="CosyVoice Adapter", version="1.0.0", lifespan=_lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "cosyvoice", "version": "1.0.0", "ready": _tts is not None}


@app.get("/ready")
async def ready():
    if _tts is None:
        raise HTTPException(status_code=503, detail="CosyVoice model not loaded")
    return {"status": "ready"}


@app.get("/version")
async def version_info():
    return {"service": "cosyvoice", "version": "1.0.0", "model": "CosyVoice2-0.5B"}


@app.post("/unload")
async def unload_model():
    """Free CosyVoice from VRAM (singing swap / sleep mode)."""
    global _tts
    if _tts is None:
        return {"status": "already_unloaded"}
    _tts = None
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    logger.info("CosyVoice unloaded from VRAM")
    return {"status": "unloaded"}


@app.post("/load")
async def load_model():
    global _tts
    if _tts is not None:
        return {"status": "already_loaded"}
    await _startup()
    if _tts is None:
        raise HTTPException(status_code=500, detail="CosyVoice failed to reload")
    return {"status": "loaded"}


@app.post("/synthesize")
async def synthesize(req: SynthesizeRequest):
    if _tts is None:
        raise HTTPException(status_code=503, detail="CosyVoice not loaded")
    try:
        prompt_path = _resolve_voice_sample(req.audio_prompt)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))

    label = req.request_id or "req"
    instruct, speed = build_instruct(req.emotion, req.emotion_intensity, req.emo_vector)
    logger.info("[%s] Synthesizing %d chars emotion=%s intensity=%d -> instruct=%r speed=%.2f",
                label, len(req.text), req.emotion, req.emotion_intensity,
                instruct[:60], speed)
    try:
        async with _synth_lock:
            wav, sr = await asyncio.to_thread(
                _run_synthesis, req.text, instruct, prompt_path, speed, label
            )
        import io
        buf = io.BytesIO()
        sf.write(buf, wav, sr, format="WAV", subtype="PCM_16")
        data = buf.getvalue()
        logger.info("[%s] Done — %.1fs audio, %d bytes", label, len(wav) / sr, len(data))
        return {"wav_base64": base64.b64encode(data).decode("ascii")}
    except Exception as e:
        logger.exception("[%s] synthesis failed", label)
        raise HTTPException(status_code=500, detail=str(e))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CosyVoice HTTP adapter for Koroki")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9004)
    return parser.parse_args()


if __name__ == "__main__":
    import uvicorn

    args = _parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
