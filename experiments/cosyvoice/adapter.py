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
from fastapi.responses import StreamingResponse
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


def _load_trt_enabled() -> bool:
    """OPT-O2: TensorRT for the flow estimator (RTF stage, not first-chunk).

    settings.yaml models.tts.cosyvoice_load_trt is the source of truth;
    COSYVOICE_LOAD_TRT env overrides for tests. Read directly (not via
    shared.utils) because this adapter deliberately has no main-repo imports.
    """
    env = os.environ.get("COSYVOICE_LOAD_TRT", "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    if env in ("0", "false", "no"):
        return False
    try:
        import yaml

        cfg = yaml.safe_load(
            (_REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
        )
        return bool(cfg.get("models", {}).get("tts", {}).get("cosyvoice_load_trt", False))
    except Exception as exc:
        logger.warning("settings.yaml read failed (%s) — load_trt off", exc)
        return False


# ── contract (mirror of experiments/index-tts/adapter.py) ────────────


class SynthesizeRequest(BaseModel):
    request_id: str = Field(default="", max_length=128)
    text: str = Field(..., min_length=1, max_length=4000)
    relationship_score: Annotated[int, Field(ge=0, le=100)] = 50
    emotion: str = Field(default="neutral", max_length=64)
    emotion_intensity: Annotated[int, Field(ge=0, le=100)] = 50
    emotion_variant: str | None = Field(default=None, max_length=64)
    audio_prompt: str | None = None
    # "instruct2" (default): instruct text carries style, reference = timbre
    # only (CosyVoice strips the reference's speech tokens from generation —
    # why she sounded "neutralized" vs the same sample on IndexTTS, owner
    # 2026-07-06). "cross_lingual": reference's PROSODY/character rides into
    # generation (the m4 "most lifeful" mode); no instruct control — emotion
    # comes from reference choice (the reference-bank plan).
    synthesis_mode: str | None = Field(default=None, max_length=24)
    # IndexTTS2 blend order: [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]
    emo_vector: list[float] | None = None


# Delivery variants — additive instruct suffixes selected via the (previously
# dormant) emotion_variant request field. First user: wavebench, whose synth
# read "too english-ed, like straight from book english learning" (owner,
# 2026-07-05) — formal text read formally. Production chat sends no variant
# and is untouched.
_VARIANT_SUFFIX: dict[str, str] = {
    "conversational": (
        " Casual everyday chat delivery — relaxed and spontaneous, like talking "
        "to a friend, never like reading a script aloud."
    ),
}


def _apply_variant(instruct: str, variant: str | None) -> str:
    extra = _VARIANT_SUFFIX.get((variant or "").strip().lower())
    if not extra:
        return instruct
    return instruct.replace("<|endofprompt|>", extra + "<|endofprompt|>")


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


# ── emotion reference bank (research arc item 2, built 2026-07-07) ───
# Koroki-voiced acted-emotion references (RAVDESS → Korokiv5 RVC, built by
# scripts/build_emotion_reference_bank.py). In cross_lingual mode the
# reference's PROSODY rides into generation (instruct2 strips it to timbre —
# LEGACY 2026-07-06), so the reference IS the emotion control. Emotions the
# bank lacks (teasing/playful/tired — no acted source) keep the default
# instruct2 path; EN_sample already carries the teasing register natively.
# Multiple actors per emotion: picker takes first alphabetical — after the
# ear-check, delete the losing actors' wavs and the winner is the pick.

_BANK_DIR = _REPO_ROOT / "voice_samples" / "emotion_bank"
_BANK_MIN_INTENSITY = 55  # mild feelings stay on the default voice
_BANK_MAP = {  # her emotion bucket → RAVDESS emotion in the bank
    "happy": "happy", "excited": "happy", "sad": "sad", "melancholic": "sad",
    "calm": "calm", "warm": "calm", "angry": "angry", "annoyed": "angry",
    "afraid": "fearful", "surprised": "surprised",
}


def _bank_enabled() -> bool:
    """settings.yaml models.tts.emotion_reference_bank; COSYVOICE_EMOTION_BANK
    env overrides for tests. Same pattern as _load_trt_enabled."""
    env = os.environ.get("COSYVOICE_EMOTION_BANK", "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    if env in ("0", "false", "no"):
        return False
    try:
        import yaml

        cfg = yaml.safe_load(
            (_REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
        )
        return bool(cfg.get("models", {}).get("tts", {}).get("emotion_reference_bank", False))
    except Exception as exc:
        logger.warning("settings.yaml read failed (%s) — emotion bank off", exc)
        return False


def _bank_reference(emotion: str, intensity: int) -> str | None:
    """Bank wav for a felt emotion, or None → default path untouched."""
    if intensity < _BANK_MIN_INTENSITY:
        return None
    key = (emotion or "").strip().lower()
    key = _BANK_MAP.get(_ALIASES.get(key, key))
    if not key:
        return None
    hits = sorted(_BANK_DIR.glob(f"{key}__actor*.wav"))
    return str(hits[0]) if hits else None


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
        # strong tier already exaggerated; extreme intensity slows/speeds a touch
        # more — CLAMPED: tired@60 compounded 0.82→0.73 and the owner heard
        # "insanely weird slowed down" (2026-07-06 tag batch #04). 0.8 is the
        # floor where slow still sounds intentional rather than broken.
        speed = max(0.8, min(1.15, speed + (speed - 1.0) * 0.5))
    return instruct + "<|endofprompt|>", round(max(0.75, min(1.15, speed)), 3)


# ── guards ───────────────────────────────────────────────────────────


def _expected_duration(text: str) -> float:
    """Rough speech-duration estimate for the duration-sanity guard.

    Floor recalibrated 2026-07-05: the old max(1.0, ...) made a natural 0.5s
    "okay" (4 chars -> expected 1.0s, lower band 0.6s) fail undershoot THREE
    times per reply — 16s of retries for a correct result, minutes under queue
    contention (the "voice is taking too long to arrive" report, 2026-07-04).
    """
    # Vocal-event tokens produce audio without "speaking" their characters —
    # count each as ~0.8s of expected sound, not as 10 letters of text.
    n_events = text.count("[laughter]") + text.count("[breath]") + text.count("[sigh]")
    text = text.replace("[laughter]", "").replace("[breath]", "").replace("[sigh]", "")
    cjk = sum(1 for ch in text if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿")
    latin = max(0, len(text) - cjk)
    return max(0.6, 0.075 * latin + 0.18 * cjk + 0.35 + 0.8 * n_events)


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
    """Max per-100ms count of violent sample-to-sample jumps, LOUDNESS-NORMALIZED.

    History: the original metric compared raw diffs against a fixed 0.25 —
    which made it a covert loudness detector: expressive tiers (louder) scored
    557-883 and overlapped the genuine mic-broke class (777-1011), so the
    guard reseeded good audio (2026-07-05, second recalibration attempt).
    Peak-normalizing first makes the score measure waveform ROUGHNESS relative
    to its own scale: broken audio keeps its structural discontinuities, loud
    clean speech drops back to the clean band.
    """
    if not len(wav):
        return 0
    peak = float(np.max(np.abs(wav)))
    if peak < 1e-6:
        return 0
    diff = np.abs(np.diff(wav / peak))
    bucket = max(1, int(0.1 * sr))
    n = len(diff) // bucket
    if n == 0:
        return 0
    counts = (diff[: n * bucket].reshape(n, bucket) > 0.25).sum(axis=1)
    return int(counts.max())


# DEMOTED TO OBSERVABILITY 2026-07-05: even normalized, known-good expressive
# speech measures up to ~700 (sibilants + dynamics read as "jumps") — the metric
# cannot separate expressive-good from broken at ANY threshold. The mic-broke
# class's root cause (tilde tokens) is fixed at the text layer, so the check
# no longer gates reseeds; it only LOGS suspicious scores to collect evidence
# for a proper spectral detector if the artifact class ever returns.
_GLITCH_LOG_THRESHOLD = 900


# ── synthesis ────────────────────────────────────────────────────────


def _synth_once(text: str, instruct: str, prompt_path: str, speed: float, seed: int,
                mode: str = "instruct2") -> np.ndarray:
    import torch
    from cosyvoice.utils.common import set_all_random_seed

    set_all_random_seed(seed)
    chunks = []
    if mode == "cross_lingual":
        # Reference-driven: the sample's prosody/character conditions generation
        # (instruct is ignored by this mode). Known quirk: occasional rushed
        # opening (owner heard it in matrix m4) — watch, don't fear.
        try:
            gen = _tts.inference_cross_lingual(text, prompt_path, stream=False, speed=speed)
        except TypeError:
            gen = _tts.inference_cross_lingual(text, prompt_path, stream=False)
    else:
        try:
            gen = _tts.inference_instruct2(text, instruct, prompt_path, stream=False, speed=speed)
        except TypeError:  # older signature without speed
            gen = _tts.inference_instruct2(text, instruct, prompt_path, stream=False)
    for out in gen:
        chunks.append(out["tts_speech"])
    return torch.cat(chunks, dim=1).squeeze(0).float().cpu().numpy()


# Textual laughs -> the model's [laughter] vocal-event token (2026-07-06 ear
# verdict: [laughter] mid-sentence renders a REAL giggle in instruct2 — m1/m3
# of the tag matrix). When she writes a laugh, her voice now actually laughs
# instead of reading "haha" aloud. Conservative: max one event per utterance,
# never at the very start (start-position tags are unreliable per repo docs).
_LAUGH_TEXT_RE = None  # compiled lazily below (module import order)


def _laughs_to_tokens(text: str) -> str:
    import re
    global _LAUGH_TEXT_RE
    if _LAUGH_TEXT_RE is None:
        _LAUGH_TEXT_RE = re.compile(
            r"(?<!\w)(?:a?ha(?:ha)+|e?he(?:he)+|ehe+|lol|lmao|ふふ+|えへへ+|あはは+)(?!\w)",
            re.IGNORECASE,
        )
    replaced = 0

    def _sub(m: "re.Match[str]") -> str:
        nonlocal replaced
        if replaced:                      # one event per utterance
            return ""                     # extra laugh text is dropped, not spoken
        replaced += 1
        return "[laughter]"

    out = re.sub(r"\s{2,}", " ", _LAUGH_TEXT_RE.sub(_sub, text)).strip()
    if not replaced:
        return text
    # Start-position tags are unreliable (and she LOVES opening with "ehehe" —
    # the first live clip lost its giggle to a silent drop here, 2026-07-06).
    # Relocate a leading token to just after the first clause boundary, or
    # after the 5th word — mid-position is the ear-verified sweet spot.
    if out.startswith("[laughter]"):
        rest = out[len("[laughter]"):].strip(" ,")
        if rest:
            m2 = re.search(r"[,.!?;、。！？]", rest)
            cut = m2.end() if m2 and len(rest[: m2.end()].split()) <= 8 else None
            if cut is None:
                words = rest.split()
                cut = len(" ".join(words[: min(5, len(words))]))
            out = (rest[:cut].rstrip() + " [laughter] " + rest[cut:].lstrip()).strip()
            out = re.sub(r"\s{2,}", " ", out)
        # text that IS only a laugh keeps the token alone — better than silence
    return out


def _normalize_text(text: str) -> str:
    import re
    text = text.strip()
    # Tildes destabilize speech-token generation — owner heard "voice breaking
    # like a broken mic" on "ohi~ genuine yeah?" (glitch bursts confirmed by
    # discontinuity analysis, 2026-07-04). Her text keeps the ~; TTS never sees it.
    text = text.replace("~", "")
    text = _laughs_to_tokens(text)
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


def _run_synthesis(text: str, instruct: str, prompt_path: str, speed: float, label: str,
                   mode: str = "instruct2") -> tuple[np.ndarray, int]:
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
        wav = _synth_once(text, instruct, prompt_path, speed, seed, mode=mode)
        dur = len(wav) / sr
        glitch = _glitch_score(wav, sr)
        if glitch >= _GLITCH_LOG_THRESHOLD:
            # Observability only — never gates (see _GLITCH_LOG_THRESHOLD note).
            logger.warning("[%s] suspicious glitch score %d (not gating)", label, glitch)
        if lo <= dur <= hi:
            best = wav
            break
        # Duration failed — keep the closest attempt as fallback.
        score = -abs(float(np.log((dur + 0.1) / expected)))
        if score > best_score:
            best, best_score = wav, score
        logger.warning("[%s] guard: %.1fs vs expected %.1fs [%.1f-%.1f] "
                       "(attempt %d) — reseeding", label, dur, expected, lo, hi,
                       attempt + 1)
    wav = _trim_tail_garbage(best, sr)
    wav = _peak_guard(wav)
    return wav, sr


_WARMUP_TEXT = (
    "Warming up the voice for the day, one full sentence with enough words in it "
    "that the generator settles into a natural rhythm."
)


# ── app ──────────────────────────────────────────────────────────────


def _sleeping_now() -> bool:
    """She's asleep and sleep-offload owns the VRAM — don't load at startup.

    Owner-found bug (2026-07-07): killing the voice process during her sleep
    made the supervisor revive it MODEL-LOADED — the offload hook only fires
    at the wake->asleep transition and never re-asserts. Reads the sleep state
    file directly (this adapter stays main-repo-import-free). Fail-open: any
    read problem means load normally.
    """
    try:
        import json as _json

        st = _json.loads(
            (_REPO_ROOT / "data" / "body" / "sleep_state.json").read_text(encoding="utf-8")
        )
        if st.get("state") not in ("asleep", "falling_asleep"):
            return False
        import yaml

        cfg = yaml.safe_load(
            (_REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
        )
        return bool(cfg.get("sleep", {}).get("vram_offload", False))
    except Exception:
        return False


async def _startup(force: bool = False):
    global _tts
    if not force and _sleeping_now():
        logger.info("she's asleep (sleep offload owns VRAM) — starting UNLOADED; "
                    "the waking hook or POST /load brings the model up")
        return
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
        use_trt = _load_trt_enabled()
        # load_trt expects flow.decoder.estimator.fp16.mygpu.plan in the model
        # dir — build it OFFLINE with scripts/build_cosyvoice_trt.py first, or
        # the first load blocks for minutes building it in-process.
        _tts = CosyVoice2(str(_MODEL_DIR), load_jit=False, load_trt=use_trt, fp16=True)
        logger.info(
            "CosyVoice2 ready in %.1fs (sr=%d, flow=%s)",
            time.time() - t0,
            _tts.sample_rate,
            "tensorrt" if use_trt else "pytorch",
        )
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
    await _startup(force=True)  # explicit load overrides the sleep check
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
    instruct = _apply_variant(instruct, req.emotion_variant)
    logger.info("[%s] Synthesizing %d chars emotion=%s intensity=%d -> instruct=%r speed=%.2f",
                label, len(req.text), req.emotion, req.emotion_intensity,
                instruct[:60], speed)
    mode = (req.synthesis_mode or "instruct2").strip().lower()
    # Emotion reference bank: strong felt emotion + a bank wav for it → the
    # reference carries the emotion (cross_lingual). Callers that pin their own
    # prompt or mode are never overridden.
    if req.audio_prompt is None and req.synthesis_mode is None and _bank_enabled():
        bank = _bank_reference(req.emotion, req.emotion_intensity)
        if bank:
            prompt_path, mode = bank, "cross_lingual"
            logger.info("[%s] emotion bank: %s -> cross_lingual", label, Path(bank).name)
    try:
        async with _synth_lock:
            wav, sr = await asyncio.to_thread(
                _run_synthesis, req.text, instruct, prompt_path, speed, label, mode
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


@app.post("/synthesize_stream")
async def synthesize_stream(req: SynthesizeRequest):
    """Streaming synthesis — raw int16 mono PCM chunks as the model generates.

    Added 2026-07-05 for the wavebench latency ladder (first-audio target
    200-500 ms). Production Koroki replies stay on /synthesize: streamed audio
    can't run the duration/glitch guards (you can't validate a partial clip),
    so this endpoint trades self-healing for latency. Same model, same lock —
    a stream holds the GPU until it finishes, exactly like an offline call.
    Sample rate rides the X-Sample-Rate header.
    """
    if _tts is None:
        raise HTTPException(status_code=503, detail="CosyVoice not loaded")
    try:
        prompt_path = _resolve_voice_sample(req.audio_prompt)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))

    label = req.request_id or "stream"
    text = _normalize_text(req.text)
    instruct, speed = build_instruct(req.emotion, req.emotion_intensity, req.emo_vector)
    instruct = _apply_variant(instruct, req.emotion_variant)
    logger.info("[%s] Stream-synthesizing %d chars emotion=%s intensity=%d speed=%.2f",
                label, len(text), req.emotion, req.emotion_intensity, speed)

    import queue as _queue
    import threading as _threading
    chunks: _queue.Queue = _queue.Queue(maxsize=16)

    def _producer() -> None:
        try:
            try:
                gen = _tts.inference_instruct2(text, instruct, prompt_path,
                                               stream=True, speed=speed)
            except TypeError:  # older signature without speed
                gen = _tts.inference_instruct2(text, instruct, prompt_path, stream=True)
            for out in gen:
                wav = out["tts_speech"].numpy().flatten()
                pcm = (np.clip(wav, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
                chunks.put(pcm)
        except Exception:
            logger.exception("[%s] stream synthesis failed", label)
        finally:
            chunks.put(None)

    async def _gen():
        async with _synth_lock:
            t0 = time.perf_counter()
            _threading.Thread(target=_producer, daemon=True, name=f"cv-stream-{label}").start()
            first = True
            while True:
                item = await asyncio.to_thread(chunks.get)
                if item is None:
                    break
                if first:
                    logger.info("[%s] first chunk after %.0f ms", label,
                                (time.perf_counter() - t0) * 1000)
                    first = False
                yield item

    return StreamingResponse(
        _gen(),
        media_type="application/octet-stream",
        headers={"X-Sample-Rate": str(_tts.sample_rate)},
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CosyVoice HTTP adapter for Koroki")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9004)
    return parser.parse_args()


if __name__ == "__main__":
    import uvicorn

    args = _parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
