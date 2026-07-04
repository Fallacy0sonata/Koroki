from __future__ import annotations

import time

from fastapi import APIRouter

from ..autonomy.scheduler import autonomy_scheduler
from ..memory.cache import user_memory_cache

router = APIRouter()


@router.get("/autonomy/status")
async def autonomy_status() -> dict:
    return autonomy_scheduler.status()


@router.post("/autonomy/tick")
async def autonomy_tick() -> dict:
    return await autonomy_scheduler.tick_once()


@router.get("/autonomy/pending/{user_id}")
async def autonomy_pending(user_id: str, consume: bool = False) -> dict:
    payload = await user_memory_cache.get(user_id)
    event = payload.get("pending_proactive_event")

    if isinstance(event, dict):
        created_at = int(event.get("created_at_ts", 0))
        ttl_s = int(event.get("expires_after_s", 0))
        if created_at > 0 and ttl_s > 0 and int(time.time()) - created_at > ttl_s:
            event = None
            payload.pop("pending_proactive_event", None)
            await user_memory_cache.upsert(user_id, payload, dirty=True)
            await user_memory_cache.flush_dirty()

    if consume and event is not None:
        payload.pop("pending_proactive_event", None)
        await user_memory_cache.upsert(user_id, payload, dirty=True)
        await user_memory_cache.flush_dirty()

    return {
        "user_id": user_id,
        "has_pending": event is not None,
        "event": event,
        "consumed": bool(consume and event is not None),
    }
