"""
Koroki agent tool executor — handles tool calls emitted by the brain.

Called after the brain completes generation. Modifies emotional state and
memory in-place before TTS and memory persistence happen downstream.
"""

import logging
from typing import Any

logger = logging.getLogger("orchestrator.tools")

_VALID_EMOTIONS = {
    "happy", "sad", "angry", "excited", "calm", "shy", "surprised",
    "gentle", "annoyed", "playful", "nostalgic", "fond", "nervous", "proud",
}

_RELATIONSHIP_DELTA_CAP = 5


def execute_tool_calls(
    tool_calls: list[dict],
    emotional_state: Any,           # EmotionalState instance
    merged_context: dict,
    request_id: str = "",
) -> list[dict]:
    """Execute tool calls from the brain, modifying state in-place.

    Returns a list of memory entries to append to core_facts for store_memory calls.
    Other tools (set_emotion, update_relationship) modify the passed objects directly.
    """
    extra_facts: list[dict] = []

    for call in tool_calls:
        name = call.get("name", "")
        args = call.get("arguments", {})

        if name == "set_emotion":
            emotion = str(args.get("emotion", "")).lower().strip()
            intensity = int(args.get("intensity", 60))
            intensity = max(1, min(100, intensity))

            if emotion not in _VALID_EMOTIONS:
                logger.warning("[%s] set_emotion: unknown emotion %r — ignoring", request_id, emotion)
                continue

            logger.info("[%s] Tool set_emotion: %s intensity=%d", request_id, emotion, intensity)
            emotional_state.current_emotion = emotion
            emotional_state.intensity = intensity

        elif name == "store_memory":
            content = str(args.get("content", "")).strip()
            importance = int(args.get("importance", 50))
            importance = max(1, min(100, importance))

            if not content:
                continue

            logger.info("[%s] Tool store_memory: importance=%d %r", request_id, importance, content[:80])
            extra_facts.append({
                "content": content,
                "importance": importance,
                "source": "koroki_explicit",
            })

        elif name == "update_relationship":
            delta = int(args.get("delta", 0))
            reason = str(args.get("reason", "")).strip()
            delta = max(-_RELATIONSHIP_DELTA_CAP, min(_RELATIONSHIP_DELTA_CAP, delta))

            if delta == 0:
                continue

            current = int(merged_context.get("relationship_score", 50))
            new_score = max(0, min(100, current + delta))
            merged_context["relationship_score"] = new_score
            logger.info(
                "[%s] Tool update_relationship: %+d → %d (%s)",
                request_id, delta, new_score, reason[:60],
            )

        else:
            logger.debug("[%s] Tool %r unknown — skipped", request_id, name)

    return extra_facts
