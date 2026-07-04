"""
Streaming endpoint for external clients.

Uses WebSockets internally for Brain -> Orchestrator transport and forwards
safe chunks outward as Server-Sent Events. If the Brain sends a coded terminate
signal, the orchestrator immediately mutes TTS and ends the stream.

Event types:
  text_chunk  — a single token or partial word from the brain
  tts_chunk   — (Phase B) a raw audio chunk from TTS
  complete    — stream finished, includes timing payload
  error       — something went wrong, stream is dead
"""

import json
import logging

import websockets
from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from ..memory.cache import user_memory_cache
from ..pipeline.slots import create_inference_slot
from ..schemas import ChatRequest
from ..telemetry.timing import RequestTiming
from ..cognition import run_cognitive_cycle
from shared.utils.config import get_settings

router = APIRouter()
logger = logging.getLogger("orchestrator.stream")


@router.post("/stream")
async def stream_chat(req: ChatRequest, request: Request) -> EventSourceResponse:
    timing = RequestTiming(request_id=req.request_id)
    settings = get_settings()
    cognition_cfg = settings.get("cognition", {})
    pre_generation_enabled = settings.get("features", {}).get("pre_generation", {}).get("enabled", False)
    cognition_enabled = bool(cognition_cfg.get("enabled", True))

    timing.mark("validate")
    cached_user_payload = await user_memory_cache.get(req.user_context.user_id)
    merged_context = req.user_context.model_dump(mode="json")
    for key in ("relationship_score", "last_summary", "core_facts", "recent_turns"):
        if key in cached_user_payload and cached_user_payload[key] not in (None, [], ""):
            merged_context[key] = cached_user_payload[key]
    if merged_context.get("is_owner", False):
        merged_context["relationship_score"] = 100
        merged_context["mode"] = "owner"
    timing.mark("memory_fetch")
    timing.mark("cognitive_observe")

    cognitive_snapshot = None
    if cognition_enabled:
        cognitive_snapshot = run_cognitive_cycle(
            user_id=req.user_context.user_id,
            user_message=req.message,
            merged_context=merged_context,
            emotional_state=(cached_user_payload.get("emotional_state") or {}),
        )
    timing.mark("cognitive_evaluate")

    slot = create_inference_slot(
        request_id=req.request_id,
        user_id=req.user_context.user_id,
        mode=merged_context.get("mode", req.user_context.mode),
        relationship_score=merged_context.get("relationship_score", req.user_context.relationship_score),
        pre_generation_enabled=pre_generation_enabled,
    )
    timing.mark("cognitive_plan")

    async def event_generator():
        brain_payload = {
            "request_id": req.request_id,
            "message": req.message,
            "user_context": merged_context,
        }

        brain_ws_url = settings["services"]["brain"]["url"].replace("http://", "ws://").replace("https://", "wss://") + "/ws/stream"
        brain_svc_cfg = settings.get("services", {}).get("brain", {})
        ws_open_timeout_s = float(brain_svc_cfg.get("ws_open_timeout_s", 30.0))
        ws_ping_interval_s = float(brain_svc_cfg.get("ws_ping_interval_s", 20.0))
        ws_ping_timeout_s = float(brain_svc_cfg.get("ws_ping_timeout_s", 120.0))

        try:
            async with websockets.connect(
                brain_ws_url,
                max_size=2**20,
                open_timeout=ws_open_timeout_s,
                ping_interval=ws_ping_interval_s,
                ping_timeout=ws_ping_timeout_s,
            ) as websocket:
                await websocket.send(json.dumps(brain_payload))

                while True:
                    if await request.is_disconnected():
                        logger.info("[%s] Client disconnected mid-stream", req.request_id)
                        await websocket.close(code=1000)
                        return

                    raw = await websocket.recv()
                    chunk_data = json.loads(raw)
                    message_type = chunk_data.get("type")

                    if message_type == "text_chunk":
                        chunk = chunk_data.get("text", "")
                        if not chunk:
                            continue
                        timing.mark_token()
                        slot.append_text(chunk)
                        yield {
                            "event": "text_chunk",
                            "data": json.dumps({
                                "request_id": req.request_id,
                                "text": chunk,
                            }),
                        }
                        continue

                    if message_type == "terminate":
                        logger.error(
                            "[%s] Brain issued coded terminate: %s (%s)",
                            req.request_id,
                            chunk_data.get("code"),
                            chunk_data.get("pattern"),
                        )
                        timings = timing.finalize()
                        yield {
                            "event": "error",
                            "data": json.dumps({
                                "request_id": req.request_id,
                                "error": chunk_data.get("code", "brain_terminate"),
                                "tts_muted": True,
                                "pattern": chunk_data.get("pattern"),
                                "timings": timings,
                            }),
                        }
                        return

                    if message_type == "complete":
                        break

                    if message_type == "error":
                        raise RuntimeError(chunk_data.get("error", "brain_stream_error"))

        except TimeoutError as exc:
            logger.error(
                "[%s] Brain stream open timeout after %ss: %s",
                req.request_id,
                ws_open_timeout_s,
                exc,
            )
            timings = timing.finalize()
            yield {
                "event": "error",
                "data": json.dumps({
                    "request_id": req.request_id,
                    "error": "brain_open_timeout",
                    "timings": timings,
                }),
            }
            return
        except Exception as exc:
            logger.error("[%s] Brain stream error: %s", req.request_id, exc)
            timings = timing.finalize()
            yield {
                "event": "error",
                "data": json.dumps({
                    "request_id": req.request_id,
                    "error": str(exc),
                    "timings": timings,
                }),
            }
            return

        # TTS streaming placeholder — Phase B will yield tts_chunk events here
        timing.mark("tts_first_chunk")

        reflective_snapshot = cognitive_snapshot
        if cognition_enabled:
            reflective_snapshot = run_cognitive_cycle(
                user_id=req.user_context.user_id,
                user_message=slot.primary_text,
                merged_context=merged_context,
                emotional_state=(cached_user_payload.get("emotional_state") or {}),
            )
            timing.set_cognitive_metrics(
                coherence=reflective_snapshot.coherence_score,
                affect_stability=reflective_snapshot.affect_stability,
                memory_coherence=reflective_snapshot.memory_coherence,
                intent_strength=reflective_snapshot.intent_strength,
                attention_entropy=reflective_snapshot.attention_entropy,
                initiative_drive=reflective_snapshot.initiative_drive,
                reflection_score=reflective_snapshot.reflection_score,
                proactive_eligible=reflective_snapshot.proactive_eligible,
                proactive_cooldown_s=reflective_snapshot.proactive_cooldown_s,
            )
        timing.mark("cognitive_reflect")

        await user_memory_cache.upsert(
            req.user_context.user_id,
            {
                "relationship_score": merged_context.get("relationship_score", req.user_context.relationship_score),
                "is_owner": merged_context.get("is_owner", req.user_context.is_owner),
                "last_summary": merged_context.get("last_summary"),
                "core_facts": merged_context.get("core_facts"),
                "recent_turns": merged_context.get("recent_turns"),
            },
            dirty=True,
        )

        timings = timing.finalize()
        yield {
            "event": "complete",
            "data": json.dumps({
                "request_id": req.request_id,
                "next_response_buffer_ready": bool(slot.next_response),
                "next_response_enabled": bool(slot.next_response and slot.next_response.enabled),
                    "cognition": {
                        "enabled": cognition_enabled,
                        "coherence_score": reflective_snapshot.coherence_score if reflective_snapshot else None,
                        "initiative_drive": reflective_snapshot.initiative_drive if reflective_snapshot else None,
                        "proactive_eligible": reflective_snapshot.proactive_eligible if reflective_snapshot else None,
                        "proactive_cooldown_s": (
                            reflective_snapshot.proactive_cooldown_s if reflective_snapshot else None
                        ),
                    },
                "timings": timings,
            }),
        }

    return EventSourceResponse(event_generator())
