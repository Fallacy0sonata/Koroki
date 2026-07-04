from services.orchestrator.cognition.engine import run_cognitive_cycle


def test_cognitive_cycle_yields_bounded_scores() -> None:
    snapshot = run_cognitive_cycle(
        user_id="u1",
        user_message="Can you explain why I feel anxious today and help me plan next steps?",
        merged_context={
            "relationship_score": 55,
            "recent_turns": [{"role": "user", "content": "hi"}] * 4,
            "core_facts": ["fact"] * 6,
        },
        emotional_state={"warmth": 56, "arousal": 48, "intensity": 57},
    )

    assert 0.0 <= snapshot.observe_score <= 1.0
    assert 0.0 <= snapshot.memory_coherence <= 1.0
    assert 0.0 <= snapshot.coherence_score <= 1.0


def test_cognitive_runtime_facts_include_core_fields() -> None:
    snapshot = run_cognitive_cycle(
        user_id="u2",
        user_message="hello",
        merged_context={"relationship_score": 10, "recent_turns": [], "core_facts": []},
        emotional_state={"warmth": 40, "arousal": 35, "intensity": 45},
    )
    facts = snapshot.to_runtime_facts()

    assert facts[0] == "## Cognitive Context"
    assert any(item.startswith("cognitive_coherence_score=") for item in facts)
    assert any(item.startswith("cognitive_proactive_cooldown_s=") for item in facts)


def test_higher_context_depth_improves_memory_coherence() -> None:
    low = run_cognitive_cycle(
        user_id="u3",
        user_message="what should we do",
        merged_context={"relationship_score": 30, "recent_turns": [], "core_facts": []},
        emotional_state={"warmth": 45, "arousal": 42, "intensity": 50},
    )
    high = run_cognitive_cycle(
        user_id="u3",
        user_message="what should we do",
        merged_context={
            "relationship_score": 30,
            "recent_turns": [{"role": "user", "content": "a"}] * 8,
            "core_facts": ["x"] * 20,
        },
        emotional_state={"warmth": 45, "arousal": 42, "intensity": 50},
    )

    assert high.memory_coherence > low.memory_coherence
