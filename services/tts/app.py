"""
TTS service — FastAPI application entry point.

Exposes:
  GET  /health
  GET  /ready
  GET  /version
  POST /v1/synthesize   — returns WAV audio bytes
  GET  /v1/profiles     — list available voice profiles
"""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .synthesis import TTSSynthesizer
from .voice_profiles import build_voice_style, get_voice_profile, list_profiles
from shared.utils.config import get_settings

# Ensure logs directory exists
_log_dir = Path(__file__).resolve().parents[1] / "data" / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(_log_dir / "tts.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("tts")

_synthesizer: TTSSynthesizer | None = None
_start_time = time.monotonic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _synthesizer
    logger.info("TTS service starting — loading model...")
    _synthesizer = TTSSynthesizer()
    _synthesizer.load()
    app.state.synthesizer = _synthesizer
    logger.info("TTS service ready. Sample rate: %d Hz", _synthesizer.sample_rate)
    yield
    logger.info("TTS service shutting down.")


app = FastAPI(title="Koroki TTS", version="2.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------

class SynthesizeRequest(BaseModel):
    request_id: str
    text: str = Field(min_length=1, max_length=4000)
    relationship_score: int = Field(ge=0, le=100)
    emotion: str = Field(default="neutral", max_length=32)
    emotion_intensity: int = Field(default=50, ge=0, le=100)
    emotion_variant: str | None = Field(default=None, max_length=64)


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "tts",
        "version": "2.0.0",
        "uptime_seconds": round(time.monotonic() - _start_time, 2),
    }


@app.get("/ready")
async def ready() -> dict:
    is_ready = _synthesizer is not None and _synthesizer.is_ready()
    return {"ready": is_ready, "checks": {"model_loaded": is_ready}}


@app.get("/version")
async def version() -> dict:
    return {"service": "tts", "version": "2.0.0", "build": "2.0.0"}


# ---------------------------------------------------------------------------
# Synthesis endpoints
# ---------------------------------------------------------------------------

@app.post("/v1/synthesize")
async def synthesize(req: SynthesizeRequest) -> Response:
    """Synthesize text to WAV audio. Returns raw WAV bytes."""
    if _synthesizer is None:
        raise HTTPException(status_code=503, detail="TTS not ready")

    request_started = time.perf_counter()
    profile = get_voice_profile(req.relationship_score)
    voice_style = build_voice_style(
        request_id=req.request_id,
        emotion=req.emotion,
        intensity=req.emotion_intensity,
        variant=req.emotion_variant,
    )
    logger.info(
        "[%s] Synthesizing %d chars with voice=%s emotion=%s intensity=%d variant=%s",
        req.request_id, len(req.text), profile.name, voice_style.emotion, voice_style.intensity, voice_style.variant,
    )

    audio_bytes = await _synthesizer.synthesize(
        req.text,
        profile.name,
        req.request_id,
        emotion=req.emotion,
        emotion_intensity=req.emotion_intensity,
        emotion_variant=voice_style.variant,
    )
    logger.info(
        "[%s] TTS REQUEST request_entry_ms=0.0 total_ms=%.1f text_chars=%d voice=%s bytes=%d",
        req.request_id,
        (time.perf_counter() - request_started) * 1000.0,
        len(req.text),
        profile.name,
        len(audio_bytes),
    )

    return Response(
        content=audio_bytes,
        media_type="audio/wav",
        headers={
            "X-Request-ID": req.request_id,
            "X-Voice-Profile": profile.name,
            "X-Emotion": voice_style.emotion,
            "X-Emotion-Variant": voice_style.variant,
            "X-Sample-Rate": str(_synthesizer.sample_rate),
        },
    )


@app.get("/v1/profiles")
async def profiles() -> dict:
    return {"profiles": list_profiles()}


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    svc = settings["services"]["tts"]
    uvicorn.run(
        "services.tts.app:app",
        host=svc["host"],
        port=svc["port"],
        reload=False,
        log_level="info",
    )
