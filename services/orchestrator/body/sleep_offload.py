"""Sleep-mode VRAM offload — "like an actual human" (owner, 2026-07-04).

While she sleeps, her voice (CosyVoice, ~2.45 GB) is unloaded from the GPU;
at ASLEEP→WAKING (the 3-minute morning fog) it reloads, so she can speak by
the time she's fully awake. Frees the overnight VRAM window for the Big
Retrain and mirrors the biology: sleeping bodies power down expensive organs.

Wired via the sleep state machine's own hooks — no polling, no cron. The
external_arousal path (someone talks to her mid-night) also enters WAKING,
so the reload covers surprise wakes too; worst case her first reply waits
~60-90 s for the model load, which reads as grogginess and is honestly kind
of in character.

Flag: settings sleep.vram_offload (default true). Vision already self-manages
(unload_after_describe); the brain must stay resident (she thinks in her sleep
— dreams, consolidation).
"""
from __future__ import annotations

import logging
import threading

import httpx

logger = logging.getLogger("orchestrator.body.sleep_offload")


def _tts_adapter_url() -> str | None:
    try:
        from shared.utils.config import get_settings
        settings = get_settings()
        if not bool(settings.get("sleep", {}).get("vram_offload", True)):
            return None
        return settings["services"]["tts"].get("adapter_url") or None
    except Exception:
        return None


def _post(url: str, path: str, label: str) -> None:
    def _run() -> None:
        try:
            with httpx.Client(timeout=180.0) as client:
                r = client.post(f"{url}{path}")
                logger.info("sleep offload: %s -> %s (%s)", label, path, r.status_code)
        except Exception as exc:
            logger.warning("sleep offload: %s failed (%s)", label, exc)

    threading.Thread(target=_run, daemon=True, name=f"sleep-offload-{label}").start()


def _on_sleep() -> None:
    url = _tts_adapter_url()
    if url:
        logger.info("she's asleep — unloading her voice (~2.5 GB VRAM back)")
        _post(url, "/unload", "voice-unload")


def _on_waking() -> None:
    url = _tts_adapter_url()
    if url:
        logger.info("she's waking — reloading her voice during the morning fog")
        _post(url, "/load", "voice-reload")


def install() -> None:
    """Register the offload hooks on the sleep state machine (idempotent-ish:
    call once from app startup)."""
    from .sleep import get_sleep
    sleep = get_sleep()
    sleep.on_sleep(_on_sleep)
    sleep.on_waking(_on_waking)
    logger.info("sleep VRAM offload installed (voice unloads while she sleeps)")
