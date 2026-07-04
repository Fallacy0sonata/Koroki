from services.brain import prompt_builder
from services.brain.prompt_builder import build_prompt


def test_emotion_continuity_block_added(monkeypatch):
    # The continuity block is a legacy-profile feature; the agent profile injects
    # emotion via felt_state instead. Pin the profile so the live settings.yaml
    # value doesn't decide which prompt path this contract exercises.
    monkeypatch.setattr(
        prompt_builder,
        "get_settings",
        lambda: {"models": {"brain": {"prompt_profile": "legacy"}}},
    )
    core_facts = [
        "## Current Emotional Context: primary=playful secondary=warmth=62 arousal=45"
    ]
    prompt = build_prompt(
        message="Hello",
        adapter_name="tsundere",
        is_owner=False,
        relationship_score=42,
        mode="casual",
        recent_turns=None,
        last_summary=None,
        core_facts=core_facts,
        tokenizer=None,
    )
    assert "## Emotional Continuity" in prompt
