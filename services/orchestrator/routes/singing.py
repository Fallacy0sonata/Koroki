"""
POST /v1/sing — dedicated singing endpoint.

Called by the Discord /sing slash command. Runs the same VRAM-swap pipeline
as the text-triggered path in chat.py but without going through LLM inference.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.orchestrator.singing.pipeline import run_singing_pipeline
from services.orchestrator.nervous_system import on_activity_event
from shared.utils.config import get_settings

logger = logging.getLogger("orchestrator.singing")
router = APIRouter()


class SingCommandRequest(BaseModel):
    song: str = Field(..., min_length=1, max_length=256)
    user_id: str = Field(default="", max_length=64)
    transpose: int = Field(default=0, ge=-24, le=24)
    vocal_only: bool = False


class SingCommandResponse(BaseModel):
    wav_base64: str
    song: str
    request_id: str


@router.post("/sing", response_model=SingCommandResponse)
async def sing_command(req: SingCommandRequest):
    settings = get_settings()
    singing_cfg = settings.get("singing", {})

    if not singing_cfg.get("enabled", False):
        raise HTTPException(status_code=503, detail="Singing is not enabled in settings.yaml")

    request_id = f"sing_{req.user_id}_{uuid.uuid4().hex[:8]}"
    transpose = req.transpose or int(singing_cfg.get("transpose", 0))
    vocal_gain = 0.0 if req.vocal_only else float(singing_cfg.get("vocal_gain", 1.2))

    logger.info("[%s] /sing command: song='%s' transpose=%d vocal_only=%s",
                request_id, req.song, transpose, req.vocal_only)

    audio_bytes = await run_singing_pipeline(
        song=req.song,
        request_id=request_id,
        settings=settings,
    )

    if not audio_bytes:
        raise HTTPException(status_code=500, detail="Singing pipeline failed — check singing adapter logs")

    on_activity_event("singing_done")

    try:
        from ..mind.journal import journal, KIND_SING
        journal().log_event(KIND_SING, f"sang '{req.song}'",
                            meta={"user": req.user_id, "request_id": request_id})
    except Exception:
        logger.debug("journal sing write skipped", exc_info=True)

    import base64
    return SingCommandResponse(
        wav_base64=base64.b64encode(audio_bytes).decode("ascii"),
        song=req.song,
        request_id=request_id,
    )
