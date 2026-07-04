from services.orchestrator.emotions.engine import (
    EmotionalState,
    compute_emotional_state,
    get_tts_hints,
    nudge_emotion,
)


def test_owner_warmth_bias_pushes_toward_caring() -> None:
    state = EmotionalState(user_id="u1")
    state = compute_emotional_state(state, relationship_score=100, is_owner=True)
    assert state.warmth >= 62
    assert state.current_emotion in {"caring", "neutral"}


def test_playful_message_raises_arousal_and_playfulness() -> None:
    state = EmotionalState(user_id="u2")
    state = nudge_emotion(state, "hehe~ come here, let me tease you a little", relationship_score=100)
    hints = get_tts_hints(state, is_owner=True, relationship_score=100)
    assert state.arousal > 40
    assert hints.primary_tag in {"playful", "caring", "whisper"}


def test_negative_low_relationship_can_turn_cold() -> None:
    state = EmotionalState(user_id="u3", warmth=40, arousal=45, dominance=60)
    state = nudge_emotion(state, "I hate this, it is awful and annoying", relationship_score=10)
    assert state.current_emotion in {"cold", "annoyed", "frustrated"}
