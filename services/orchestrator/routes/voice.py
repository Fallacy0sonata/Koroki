from __future__ import annotations

import asyncio
import base64
import logging
import re
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException

from ..schemas import DeferredTTSRequest, DeferredTTSResponse
from shared.utils.config import get_settings

router = APIRouter()
logger = logging.getLogger("orchestrator.voice")

_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


def _sanitize_request_id(request_id: str) -> str:
    cleaned = _SAFE_NAME_PATTERN.sub("_", request_id).strip("._")
    return cleaned[:96] or "voice"


@router.post("/voice", response_model=DeferredTTSResponse)
async def synthesize_deferred_voice(req: DeferredTTSRequest) -> DeferredTTSResponse:
    repo_root = Path(__file__).resolve().parents[3]
    assets_root = repo_root / "assets" / "generated" / "audio"
    assets_root.mkdir(parents=True, exist_ok=True)

    safe_request_id = _sanitize_request_id(req.request_id)
    output_name = f"{safe_request_id}_{uuid.uuid4().hex[:8]}.wav"
    output_path = assets_root / output_name

    logger.info("[%s] Voice synthesis starting", req.request_id)

    settings = get_settings()
    adapter_url = settings.get("services", {}).get("tts", {}).get("adapter_url", "")
    if not adapter_url:
        raise HTTPException(status_code=500, detail="IndexTTS adapter_url not configured in settings.yaml")

    synth_url = f"{adapter_url}/synthesize"
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                synth_url,
                json={
                    "request_id": req.request_id,
                    "text": req.text,
                    "relationship_score": req.relationship_score,
                    "emotion": req.emotion,
                    "emotion_intensity": req.emotion_intensity,
                    "emotion_variant": req.emotion_variant,
                },
            )
            response.raise_for_status()
            data = response.json()
            wav_bytes = base64.b64decode(data["wav_base64"])
            output_path.write_bytes(wav_bytes)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        detail = "IndexTTS model not loaded — check checkpoints" if status == 503 else f"IndexTTS error {status}"
        logger.error("[%s] IndexTTS returned %s", req.request_id, status)
        raise HTTPException(status_code=502, detail=detail) from exc
    except Exception as exc:
        logger.error("[%s] IndexTTS unavailable: %s", req.request_id, exc)
        raise HTTPException(status_code=503, detail="IndexTTS adapter unreachable") from exc

    if not output_path.exists():
        raise HTTPException(status_code=500, detail="Voice file was not created")

    logger.info("[%s] Voice ready: %s (%d bytes)", req.request_id, output_path, len(wav_bytes))
    return DeferredTTSResponse(
        request_id=req.request_id,
        audio_url=f"/assets/generated/audio/{output_name}",
        audio_path=str(output_path),
    )
