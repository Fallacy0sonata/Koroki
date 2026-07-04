"""
Singing pipeline for Koroki Ascension 2.0.

Handles VRAM swap (IndexTTS ↔ RVC) and delegates to the singing adapter.
Called from the orchestrator chat route when singing intent is detected.
"""

from __future__ import annotations

import asyncio
import base64
import logging

import httpx

logger = logging.getLogger("orchestrator.singing.pipeline")


async def run_singing_pipeline(
    song: str,
    request_id: str,
    settings: dict,
) -> bytes | None:
    """Full singing pipeline: VRAM swap → sing → swap back → return audio bytes.

    Returns raw WAV bytes, or None on failure (caller falls back to normal TTS).
    """
    singing_cfg = settings.get("singing", {})
    indextts_url: str = settings["services"]["tts"].get("adapter_url", "")
    singing_url: str = singing_cfg.get("adapter_url", "http://127.0.0.1:9001")
    # When using the v2 (Seed-VC) adapter, prefer singing_v2 config for v2-specific params
    if ":9002" in singing_url:
        v2_cfg = settings.get("singing_v2", {})
        transpose: int = int(v2_cfg.get("transpose", singing_cfg.get("transpose", 0)))
        vocal_gain: float = float(v2_cfg.get("vocal_gain", singing_cfg.get("vocal_gain", 1.2)))
        vram_swap: bool = v2_cfg.get("vram_swap_enabled", singing_cfg.get("vram_swap_enabled", True))
        reload_bg: bool = v2_cfg.get("reload_indextts_background", singing_cfg.get("reload_indextts_background", True))
        timeout: float = float(v2_cfg.get("pipeline_timeout_s", singing_cfg.get("pipeline_timeout_s", 900)))
    else:
        transpose = int(singing_cfg.get("transpose", 0))
        vocal_gain = float(singing_cfg.get("vocal_gain", 1.2))
        vram_swap = singing_cfg.get("vram_swap_enabled", True)
        reload_bg = singing_cfg.get("reload_indextts_background", True)
        timeout = float(singing_cfg.get("pipeline_timeout_s", 300))

    async with httpx.AsyncClient(timeout=timeout) as client:
        # Step 1: unload IndexTTS to free VRAM
        if vram_swap and indextts_url:
            try:
                await client.post(f"{indextts_url}/unload")
                logger.info("[%s] IndexTTS unloaded for singing VRAM swap", request_id)
            except Exception as exc:
                logger.warning("[%s] Could not unload IndexTTS: %s", request_id, exc)

        # Step 2: load RVC model
        try:
            load_resp = await client.post(f"{singing_url}/load", json={})
            if load_resp.status_code not in (200, 400):
                logger.error("[%s] Singing /load failed: %s", request_id, load_resp.text)
                await _reload_indextts(client, indextts_url, request_id)
                return None
        except Exception as exc:
            logger.error("[%s] Singing adapter unreachable: %s", request_id, exc)
            await _reload_indextts(client, indextts_url, request_id)
            return None

        # Step 3: run the singing pipeline
        audio_bytes: bytes | None = None
        try:
            sing_resp = await client.post(
                f"{singing_url}/sing",
                json={
                    "request_id": request_id,
                    "song": song,
                    "transpose": transpose,
                    "vocal_gain": vocal_gain,
                },
                timeout=timeout,
            )
            sing_resp.raise_for_status()
            audio_bytes = base64.b64decode(sing_resp.json()["wav_base64"])
            logger.info("[%s] Singing pipeline done — %d bytes for '%s'", request_id, len(audio_bytes), song)
        except Exception as exc:
            logger.error("[%s] Singing /sing failed: %s", request_id, exc)

        # Step 4: unload RVC model
        try:
            await client.post(f"{singing_url}/unload")
        except Exception as exc:
            logger.warning("[%s] Could not unload RVC: %s", request_id, exc)

        # Step 5: reload IndexTTS (background if configured, else blocking)
        if vram_swap and indextts_url:
            if reload_bg:
                asyncio.create_task(_reload_indextts_bg(indextts_url, request_id))
            else:
                await _reload_indextts(client, indextts_url, request_id)

        return audio_bytes


async def _reload_indextts(client: httpx.AsyncClient, indextts_url: str, request_id: str) -> None:
    if not indextts_url:
        return
    try:
        await client.post(f"{indextts_url}/load", timeout=120.0)
        logger.info("[%s] IndexTTS reloaded", request_id)
    except Exception as exc:
        logger.warning("[%s] IndexTTS reload failed: %s", request_id, exc)


async def _reload_indextts_bg(indextts_url: str, request_id: str) -> None:
    """Reload IndexTTS in background so the singing response isn't delayed."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        await _reload_indextts(client, indextts_url, request_id)
