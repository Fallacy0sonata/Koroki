from services.orchestrator.emotions.engine import (
    EmotionalState,
    compute_emotional_state,
    get_emotion_context_string,
    nudge_emotion,
)


def test_emotion_state_serializes_vector() -> None:
    state = EmotionalState(user_id="u1")
    payload = state.to_dict()

    assert "affect_vector" in payload
    assert "mood_state" in payload
    assert set(payload["affect_vector"].keys()) >= {"valence", "attachment", "irritation", "stress"}


def test_game_ragebait_increases_irritation_without_breaking_personality() -> None:
    state = EmotionalState(user_id="u2")
    state = nudge_emotion(
        state,
        "That game match was pure ragebait trash and the troll kept baiting me",
        relationship_score=25,
    )

    assert state.affect_vector["irritation"] >= 30
    assert state.affect_vector["stress"] >= 25
    assert state.current_emotion in {"annoyed", "frustrated", "ragebaited", "neutral", "overstimulated"}


def test_emotion_context_includes_vector_and_mood() -> None:
    state = EmotionalState(user_id="u3")
    state = compute_emotional_state(state, relationship_score=80, is_owner=True)
    context = get_emotion_context_string(state, is_owner=True)

    assert "Affect:" in context
    assert "valence=" in context
    assert "mood:" in context
