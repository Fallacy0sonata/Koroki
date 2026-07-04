import time
import logging

import httpx
from fastapi import APIRouter

from ..schemas import HealthResponse, ReadyResponse, VersionResponse
from ..nervous_system import get_state, build_state_block
from shared.utils.config import get_settings

router = APIRouter()
logger = logging.getLogger("orchestrator.health")

SERVICE_VERSION = "2.0.0"
_start_time = time.monotonic()


async def _ping_upstream(url: str) -> bool:
    """Return True if the upstream service /health responds 200."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{url}/health")
            return resp.status_code == 200
    except Exception:
        return False


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="orchestrator",
        version=SERVICE_VERSION,
        uptime_seconds=round(time.monotonic() - _start_time, 2),
    )


@router.get("/ready", response_model=ReadyResponse)
async def ready() -> ReadyResponse:
    settings = get_settings()
    brain_ok = await _ping_upstream(settings["services"]["brain"]["url"])
    # readiness must probe the engine actually in production: the IndexTTS adapter when
    # configured, legacy QwenTTS otherwise (pre-fix this pinged the dead legacy port and
    # reported tts:false while chat voice worked fine)
    tts_target = settings["services"]["tts"].get("adapter_url") or settings["services"]["tts"]["url"]
    tts_ok = await _ping_upstream(tts_target)
    checks = {"brain": brain_ok, "tts": tts_ok}
    return ReadyResponse(ready=all(checks.values()), checks=checks)


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    return VersionResponse(service="orchestrator", version=SERVICE_VERSION)


@router.get("/v1/nervstate")
async def nervstate() -> dict:
    """Current nervous system state — used by Discord bot for status updates."""
    state = get_state()
    return {"state": state, "block": build_state_block(state)}
