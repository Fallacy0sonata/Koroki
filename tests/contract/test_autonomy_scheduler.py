from services.orchestrator.autonomy.scheduler import (
    MAX_UNANSWERED_REACHOUTS,
    ProactiveDecision,
    build_proactive_signal,
    evaluate_proactive_decision,
    run_cognitive_cycle,
    unanswered_reachout_streak,
)


def test_proactive_decision_blocks_when_cooldown_active() -> None:
    snapshot = run_cognitive_cycle(
        user_id="u1",
        user_message="Can you help me plan the next step?",
        merged_context={
            "relationship_score": 70,
            "recent_turns": [{"role": "user", "content": "hello"}] * 6,
            "core_facts": ["fact"] * 8,
        },
        emotional_state={"warmth": 70, "arousal": 52, "intensity": 60},
    )
    decision = evaluate_proactive_decision(
        now_ts=10_000,
        last_emit_ts=9_900,
        has_pending_event=False,
        snapshot=snapshot,
    )

    assert isinstance(decision, ProactiveDecision)
    assert decision.should_emit is False
    assert decision.cooldown_remaining_s >= 0


def test_proactive_decision_blocks_with_pending_event() -> None:
    snapshot = run_cognitive_cycle(
        user_id="u2",
        user_message="Why do I keep overthinking this?",
        merged_context={
            "relationship_score": 55,
            "recent_turns": [{"role": "user", "content": "a"}] * 8,
            "core_facts": ["x"] * 20,
        },
        emotional_state={"warmth": 58, "arousal": 48, "intensity": 55},
    )

    decision = evaluate_proactive_decision(
        now_ts=20_000,
        last_emit_ts=0,
        has_pending_event=True,
        snapshot=snapshot,
    )

    assert decision.should_emit is False
    assert decision.cooldown_remaining_s >= 0


# ── Double-text cap (2026-07-04: four unanswered reach-outs in one afternoon) ──


def test_unanswered_streak_counts_trailing_assistant_turns() -> None:
    assert unanswered_reachout_streak(None) == 0
    assert unanswered_reachout_streak([]) == 0
    assert unanswered_reachout_streak([{"role": "user", "content": "hi"}]) == 0
    assert unanswered_reachout_streak(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey"}]
    ) == 1
    # Only TRAILING assistant turns count — an answered reach-out resets the streak.
    assert unanswered_reachout_streak(
        [
            {"role": "assistant", "content": "reach-out 1"},
            {"role": "user", "content": "reply"},
            {"role": "assistant", "content": "answer"},
            {"role": "assistant", "content": "reach-out 2"},
        ]
    ) == 2


def test_proactive_decision_blocks_after_unanswered_cap() -> None:
    snapshot = run_cognitive_cycle(
        user_id="u3",
        user_message="tell me about your day",
        merged_context={
            "relationship_score": 80,
            "recent_turns": [{"role": "user", "content": "hello"}] * 6,
            "core_facts": ["fact"] * 8,
        },
        emotional_state={"warmth": 70, "arousal": 52, "intensity": 60},
    )
    decision = evaluate_proactive_decision(
        now_ts=100_000,
        last_emit_ts=0,  # cooldown long expired — only the cap can block
        has_pending_event=False,
        snapshot=snapshot,
        unanswered_streak=MAX_UNANSWERED_REACHOUTS,
    )
    assert decision.should_emit is False

    # One below the cap must fall through to the normal eligibility path.
    below = evaluate_proactive_decision(
        now_ts=100_000,
        last_emit_ts=0,
        has_pending_event=False,
        snapshot=snapshot,
        unanswered_streak=MAX_UNANSWERED_REACHOUTS - 1,
    )
    assert below.should_emit == bool(snapshot.proactive_eligible)


# ── [system] signal builder (replaces the fake "..." user message) ──


def test_proactive_signal_is_marked_as_system_voice() -> None:
    signal = build_proactive_signal({})
    assert signal.startswith("[system]")
    assert "[silent]" in signal
    assert "..." not in signal


def test_proactive_signal_uses_override_context() -> None:
    signal = build_proactive_signal({}, override_context="koroki_reach_out=true | greet them")
    assert signal == "[system] koroki_reach_out=true | greet them"


def test_proactive_signal_anchors_to_top_salience_topic() -> None:
    payload = {
        "episodic_memory": [
            {"salience": 0.2, "topics": ["cooking"]},
            {"salience": 0.9, "topics": ["chess"]},
        ]
    }
    signal = build_proactive_signal(payload)
    assert "chess" in signal
    assert "cooking" not in signal


def test_proactive_signal_mentions_unanswered_streak() -> None:
    one_unanswered = {"recent_turns": [{"role": "assistant", "content": "reach-out"}]}
    signal = build_proactive_signal(one_unanswered)
    assert "haven't replied" in signal

    capped = {"recent_turns": [{"role": "assistant", "content": "x"}] * MAX_UNANSWERED_REACHOUTS}
    signal_capped = build_proactive_signal(capped)
    assert "[silent]" in signal_capped
    assert "never answered" in signal_capped
