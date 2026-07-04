"""Emotional state management for persistent per-user moods."""

from .engine import (
    EmotionalState,
    compute_emotional_state,
    get_emotion_context_string,
    get_tts_hints,
    nudge_emotion,
)

__all__ = [
    "EmotionalState",
    "compute_emotional_state",
    "nudge_emotion",
    "get_emotion_context_string",
    "get_tts_hints",
]
