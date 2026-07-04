from datetime import datetime, timedelta, timezone

from services.orchestrator.memory.intelligence import decay_memory_state, consolidate_user_memory


def test_decay_prunes_stale_low_salience_episode() -> None:
    old_time = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    payload = {
        "episodic_memory": [
            {"created_at": old_time, "summary": "old note", "salience": 0.01},
            {"created_at": old_time, "summary": "important note", "salience": 0.9},
        ],
        "beliefs": [
            {"claim": "often engages with games", "confidence": 0.02, "last_seen": old_time},
            {"claim": "often engages with art", "confidence": 0.8, "last_seen": old_time},
        ],
    }

    decayed = decay_memory_state(payload, now=datetime.now(timezone.utc), episode_half_life_days=14, belief_half_life_days=30)

    assert len(decayed["episodic_memory"]) == 1
    assert len(decayed["beliefs"]) == 1
    assert decayed["episodic_memory"][0]["summary"] == "important note"


def test_consolidation_tracks_belief_revisions() -> None:
    payload = {
        "episodic_memory": [],
        "beliefs": [
            {
                "claim": "often engages with games",
                "topic": "games",
                "confidence": 0.7,
                "support_count": 3,
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "emotion": "caring",
            }
        ],
        "belief_revisions": [],
        "semantic_identity": [],
        "recurring_topics": [],
        "self_model_summary": "",
    }

    result = consolidate_user_memory(
        payload,
        user_message="I want to build a new game system.",
        response_text="Then we can outline the game system carefully.",
        emotional_state={"current_emotion": "playful", "warmth": 60, "arousal": 52, "dominance": 58},
        cognitive_snapshot=None,
    )

    assert payload["beliefs"]
    assert payload["belief_revisions"]
    assert any(item["claim"].startswith("often engages with") for item in payload["beliefs"])
    assert result.semantic_facts
