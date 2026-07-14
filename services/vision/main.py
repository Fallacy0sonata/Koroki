"""Koroki Vision Service — FastAPI adapter on port 9005.

POST /v1/describe     — image(s) in, description out (caption, or answer if a
                        question is given). This is the whole transport-agnostic
                        contract: bytes in, percept out.
Game session state    — /v1/game/enter | /v1/game/exit | GET /v1/game.
                        While a game is active every describe() is conditioned
                        on it ("this is a screenshot from <game>") — she
                        identifies the game once at entry, not per frame
                        (owner design, docs/master_queue.md Sight).
POST /v1/unload       — manual VRAM release (idle watchdog does it anyway).

Runs in the MAIN .venv (Python 3.12, torch cu128, torchao).
Start: .\\scripts\\easy_start_vision_adapter.ps1
"""

from __future__ import annotations

import base64
import binascii
import logging
import time
from typing import Optional

import anyio
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from shared.utils.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("koroki.vision")

SERVICE_VERSION = "1.0.0"

app = FastAPI(title="Koroki Vision Service", version=SERVICE_VERSION)

_engine = None
_game_session: Optional[dict] = None


def get_engine():
    global _engine
    if _engine is None:
        from .engine import VisionEngine

        settings = get_settings()
        vcfg = settings.get("vision") or {}
        _engine = VisionEngine(
            model_dir=vcfg.get("model_dir", "tools/models/moondream2-2025-06-21"),
            quant=vcfg.get("quant", "int4"),
            max_edge=int(vcfg.get("max_edge", 1536)),
            idle_unload_seconds=int(vcfg.get("idle_unload_seconds", 300)),
            max_answer_tokens=int(vcfg.get("max_answer_tokens", 192)),
        )
    return _engine


class DescribeRequest(BaseModel):
    request_id: str = Field(default="", max_length=128)
    images_b64: list[str] = Field(..., min_length=1, max_length=2)
    question: Optional[str] = Field(default=None, max_length=500)
    max_tokens: Optional[int] = Field(default=None, ge=16, le=768)

    @field_validator("images_b64")
    @classmethod
    def limit_image_size(cls, v: list[str]) -> list[str]:
        for s in v:
            if not s or len(s) > 12_000_000:
                raise ValueError("image exceeds maximum encoded size (~9 MB)")
        return v


class DescribeResponse(BaseModel):
    description: str
    per_image: list[str]
    game: Optional[str] = None
    was_cold_load: bool
    latency_ms: float


class GameEnterRequest(BaseModel):
    game: str = Field(..., min_length=1, max_length=120)
    facts: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("facts")
    @classmethod
    def limit_fact_length(cls, v: list[str]) -> list[str]:
        return [f[:300] for f in v]


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict:
    # Lazy design: the service is ready even with the model unloaded — it loads
    # on the first look. model_loaded tells you whether VRAM is occupied NOW.
    eng = get_engine()
    return {"ready": True, "model_loaded": eng.model_loaded, "load_count": eng.load_count}


@app.get("/version")
async def version() -> dict:
    settings = get_settings()
    vcfg = settings.get("vision") or {}
    return {
        "service": "vision",
        "version": SERVICE_VERSION,
        "model": vcfg.get("model_dir", "").rstrip("/\\").split("\\")[-1].split("/")[-1],
        "quant": vcfg.get("quant", "int4"),
    }


def _conditioned_question(question: Optional[str]) -> Optional[str]:
    """Prefix the ask with the active game context (owner's stateful-game design)."""
    if _game_session is None:
        return question
    prefix = f"This is a screenshot from the game '{_game_session['game']}'."
    facts = _game_session.get("facts") or []
    if facts:
        prefix += " Known context: " + " ".join(facts[:6])
    if question:
        return f"{prefix} {question}"
    return f"{prefix} Describe what is happening on screen."


@app.post("/v1/describe", response_model=DescribeResponse)
async def describe(req: DescribeRequest, background_tasks: BackgroundTasks) -> DescribeResponse:
    try:
        images = [base64.b64decode(s, validate=True) for s in req.images_b64]
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid base64 image: {exc}") from exc

    eng = get_engine()
    was_cold = not eng.model_loaded
    question = _conditioned_question(req.question)

    t0 = time.time()
    try:
        per_image = await anyio.to_thread.run_sync(
            lambda: eng.describe(images, question=question, max_tokens=req.max_tokens)
        )
    except Exception as exc:
        logger.error("[%s] describe failed: %s", req.request_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"vision inference failed: {exc}") from exc
    latency_ms = (time.time() - t0) * 1000.0

    if len(per_image) == 1:
        description = per_image[0]
    else:
        description = " / ".join(
            f"image {i + 1}: {text}" for i, text in enumerate(per_image)
        )
    logger.info(
        "[%s] described %d image(s) in %.0f ms (cold=%s): %s",
        req.request_id, len(per_image), latency_ms, was_cold, description[:120],
    )
    # Sporadic-look mode (Discord): release VRAM right away so a following TTS
    # synthesis never runs at ~11.5 GB (wedge territory). Game sessions — with
    # continuous frames — set unload_after_describe: false and rely on the idle
    # watchdog instead. No game session active = always honor the flag.
    vcfg = get_settings().get("vision") or {}
    if _game_session is None and bool(vcfg.get("unload_after_describe", True)):
        background_tasks.add_task(get_engine().unload)
    return DescribeResponse(
        description=description,
        per_image=per_image,
        game=_game_session["game"] if _game_session else None,
        was_cold_load=was_cold,
        latency_ms=round(latency_ms, 1),
    )


class PointRequest(BaseModel):
    request_id: str = Field(default="", max_length=128)
    image_b64: str = Field(..., min_length=1, max_length=12_000_000)
    target: str = Field(..., min_length=1, max_length=200)


@app.post("/v1/point")
async def point(req: PointRequest) -> dict:
    """Locate a described object — normalized coords for the game-hands executor."""
    try:
        image = base64.b64decode(req.image_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid base64 image: {exc}") from exc
    eng = get_engine()
    t0 = time.time()
    try:
        points = await anyio.to_thread.run_sync(lambda: eng.point(image, req.target))
    except Exception as exc:
        logger.error("[%s] point failed: %s", req.request_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"point failed: {exc}") from exc
    logger.info("[%s] point %r -> %d hit(s) in %.0f ms",
                req.request_id, req.target, len(points), (time.time() - t0) * 1000)
    # Pointing is a game-session operation — never auto-unload here (the game
    # session keeps residency; sporadic use pays cold loads, that's fine).
    return {"points": points, "count": len(points)}


class OcrRequest(BaseModel):
    request_id: str = Field(default="", max_length=128)
    image_b64: str = Field(..., min_length=1, max_length=12_000_000)


@app.post("/v1/ocr")
async def ocr(req: OcrRequest) -> dict:
    """Full-frame game-UI OCR — exact text/numbers, CPU-only, ~300 ms.

    Division of labor (2026-07-06, bench-driven): OCR reads WHAT the screen
    says, moondream reads what it MEANS (/v1/describe) and WHERE (/v1/point).
    No VLM load, no VRAM, no game-session coupling.
    """
    from .ocr import get_ocr

    try:
        image = base64.b64decode(req.image_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid base64 image: {exc}") from exc
    t0 = time.time()
    try:
        lines = await anyio.to_thread.run_sync(lambda: get_ocr().read(image))
    except Exception as exc:
        logger.error("[%s] ocr failed: %s", req.request_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"ocr failed: {exc}") from exc
    logger.info("[%s] ocr -> %d lines in %.0f ms",
                req.request_id, len(lines), (time.time() - t0) * 1000)
    return {"lines": lines, "full_text": " | ".join(l["text"] for l in lines)}


@app.post("/v1/game/enter")
async def game_enter(req: GameEnterRequest) -> dict:
    global _game_session
    _game_session = {"game": req.game, "facts": list(req.facts), "entered_at": time.time()}
    logger.info("game session ENTER: %s (%d facts)", req.game, len(req.facts))
    return {"ok": True, "game": req.game}


@app.post("/v1/game/exit")
async def game_exit() -> dict:
    global _game_session
    prev = _game_session["game"] if _game_session else None
    _game_session = None
    logger.info("game session EXIT: %s", prev)
    return {"ok": True, "was": prev}


@app.get("/v1/game")
async def game_state() -> dict:
    if _game_session is None:
        return {"active": False}
    return {
        "active": True,
        "game": _game_session["game"],
        "facts": _game_session.get("facts") or [],
        "entered_at": _game_session["entered_at"],
    }


@app.post("/v1/unload")
async def unload() -> dict:
    return {"unloaded": get_engine().unload()}


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    scfg = (settings.get("services") or {}).get("vision") or {}
    uvicorn.run(app, host=scfg.get("host", "127.0.0.1"), port=int(scfg.get("port", 9005)))
