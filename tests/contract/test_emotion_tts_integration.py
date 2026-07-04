import pytest
from services.orchestrator.emotions.tts_integration import (
    vector_to_emotion_tags,
    emotion_tags_to_prompt_hints,
    EmotionTagHint,
)


def test_vector_to_tags_high_playfulness_owner():
    """High playfulness + high relationship should yield caring+affectionate (warmth+trust dominant)."""
    affect_vector = {
        "playfulness": 80,
        "warmth": 75,
        "trust": 85,
        "curiosity": 60,
        "irritation": 10,
        "fatigue": 20,
    }
    tags = vector_to_emotion_tags(
        affect_vector=affect_vector,
        mood_state="elevated",
        relationship_score=90,
        max_tags=3,
    )
    assert len(tags) > 0
    # High warmth+trust should yield caring as primary (overrides pure playfulness)
    assert tags[0].primary == "caring"
    # Should have affectionate for high relationship
    assert any(t.primary == "affectionate" for t in tags)


def test_vector_to_tags_high_irritation():
    """High irritation should yield annoyed."""
    affect_vector = {
        "playfulness": 20,
        "warmth": 30,
        "trust": 40,
        "curiosity": 25,
        "irritation": 85,
        "fatigue": 15,
    }
    tags = vector_to_emotion_tags(
        affect_vector=affect_vector,
        mood_state="reactive",
        relationship_score=30,
        max_tags=2,
    )
    assert len(tags) > 0
    assert tags[0].primary == "annoyed"
    assert tags[0].intensity >= 80


def test_vector_to_tags_tired():
    """High fatigue should yield tired."""
    affect_vector = {
        "playfulness": 10,
        "warmth": 40,
        "trust": 50,
        "curiosity": 20,
        "irritation": 30,
        "fatigue": 75,
    }
    tags = vector_to_emotion_tags(
        affect_vector=affect_vector,
        mood_state="tired",
        relationship_score=50,
        max_tags=2,
    )
    assert len(tags) > 0
    assert tags[0].primary == "tired"


def test_emotion_tags_to_prompt_hints():
    """Emotion tags should convert to [emo:*N] prompt hints."""
    tags = [
        EmotionTagHint(primary="playful", intensity=75, secondary="teasing"),
        EmotionTagHint(primary="caring", intensity=60, secondary="tender"),
    ]
    hints = emotion_tags_to_prompt_hints(tags)
    assert len(hints) > 0
    assert "[emo:playful" in hints[0]
    assert "[emo:caring" in hints[1] or "[emo:teasing" in hints[1]


def test_emotion_tags_intensity_levels():
    """Intensity 0-100 should map to levels 1-4."""
    tags = [
        EmotionTagHint(primary="playful", intensity=100),  # Level 4
        EmotionTagHint(primary="caring", intensity=50),  # Level 2-3
        EmotionTagHint(primary="cold", intensity=10),  # Level 1
    ]
    hints = emotion_tags_to_prompt_hints(tags)
    # Should have different level suffixes
    assert any("4" in h for h in hints)  # 100 -> level 4
    assert any("1" in h for h in hints)  # 10 -> level 1


def test_low_relationship_reserved():
    """Low relationship score should yield reserved voice."""
    affect_vector = {
        "playfulness": 50,
        "warmth": 40,
        "trust": 50,
        "curiosity": 45,
        "irritation": 20,
        "fatigue": 25,
    }
    tags = vector_to_emotion_tags(
        affect_vector=affect_vector,
        mood_state="baseline",
        relationship_score=15,
        max_tags=3,
    )
    # Should have reserved tag
    assert any(t.primary == "reserved" for t in tags)
