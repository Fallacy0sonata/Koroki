from services.orchestrator.cognition.engine import run_cognitive_cycle
from services.orchestrator.memory.intelligence import (
    build_memory_rag_facts,
    consolidate_user_memory,
)


def test_memory_consolidation_adds_episode_and_identity() -> None:
    payload = {
        "relationship_score": 62,
        "episodic_memory": [],
        "semantic_identity": [],
        "recurring_topics": [],
        "self_model_summary": "",
        "emotional_state": {"current_emotion": "caring", "warmth": 70, "arousal": 44, "dominance": 58},
    }
    snapshot = run_cognitive_cycle(
        user_id="u1",
        user_message="Can you help me plan my next game idea?",
        merged_context={
            "relationship_score": 62,
            "recent_turns": [{"role": "user", "content": "hello"}] * 5,
            "core_facts": ["fact"] * 5,
        },
        emotional_state={"warmth": 70, "arousal": 44, "intensity": 65},
    )

    result = consolidate_user_memory(
        payload,
        user_message="Can you help me plan my next game idea?",
        response_text="Yes. Let's shape a small strategy around the mechanics first.",
        emotional_state=payload["emotional_state"],
        cognitive_snapshot=snapshot,
    )

    assert payload["episodic_memory"]
    assert payload["semantic_identity"]
    assert payload["recurring_topics"]
    assert payload["self_model_summary"]
    assert result.semantic_facts


def test_memory_rag_facts_are_bounded_and_personalized() -> None:
    payload = {
        "relationship_score": 80,
        "episodic_memory": [
            {"summary": "Discussed building a game loop", "salience": 0.95},
            {"summary": "Talked about TTS emotion", "salience": 0.75},
        ],
        "semantic_identity": ["prefers structured planning", "responds strongly to emotional nuance"],
        "recurring_topics": ["game loops", "emotional nuance"],
        "self_model_summary": "Koroki remembers and adapts.",
        "emotional_state": {"current_emotion": "playful", "warmth": 66, "arousal": 55, "dominance": 60},
    }

    facts = build_memory_rag_facts(payload, max_facts=5)

    assert len(facts) <= 5
    assert any(item.startswith("my_current_state=") for item in facts)
    assert any(item.startswith("identity_trait=") for item in facts)
    assert any(item.startswith("episode=") for item in facts)
