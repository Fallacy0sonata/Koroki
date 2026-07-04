"""Vision sense — client for the vision service (:9005, moondream2).

Fail-soft by contract: if the service is disabled, down, or slow, chat proceeds
without sight (the pipeline injects an honest "you can't see it" fact instead).
The first look after idle pays a cold model load (~11 s) — the read timeout
covers it.
"""

from __future__ import annotations

import logging

import httpx

from shared.utils.config import get_settings

logger = logging.getLogger("koroki.senses.vision")


def vision_enabled() -> bool:
    return bool((get_settings().get("vision") or {}).get("enabled", False))


async def describe_images(images_b64: list[str], request_id: str = "") -> str | None:
    """Return a text percept for the attached image(s), or None if she can't see."""
    settings = get_settings()
    vcfg = settings.get("vision") or {}
    if not vcfg.get("enabled", False):
        return None
    url = ((settings.get("services") or {}).get("vision") or {}).get(
        "url", "http://127.0.0.1:9005"
    )
    timeout = httpx.Timeout(
        connect=3.0,
        read=float(vcfg.get("describe_timeout_seconds", 60)),
        write=30.0,
        pool=5.0,
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{url}/v1/describe",
                json={"request_id": request_id, "images_b64": images_b64},
            )
            if resp.status_code != 200:
                logger.warning(
                    "[%s] vision service returned %d: %s",
                    request_id, resp.status_code, resp.text[:200],
                )
                return None
            description = str(resp.json().get("description") or "").strip()
            return description or None
    except httpx.HTTPError as exc:
        logger.warning("[%s] vision service unreachable: %s", request_id, exc)
        return None
