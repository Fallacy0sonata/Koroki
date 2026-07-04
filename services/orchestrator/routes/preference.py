"""POST /v1/preference — label a Brain response as chosen or rejected for DPO training."""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..telemetry import preference_logger

router = APIRouter()
logger = logging.getLogger("orchestrator.preference")


class PreferenceRequest(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=128)
    preference: str = Field(..., pattern="^(chosen|rejected)$")


@router.post("/preference")
async def set_preference(req: PreferenceRequest) -> dict:
    ok = await preference_logger.set_preference(req.request_id, req.preference)
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid preference value")
    logger.info("Preference labeled: %s → %s", req.request_id, req.preference)
    return {"status": "ok", "request_id": req.request_id, "preference": req.preference}
