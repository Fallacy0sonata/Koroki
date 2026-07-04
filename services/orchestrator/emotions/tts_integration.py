"""
Emotion vector to TTS emotion-tag conversion.
Maps Koroki's internal emotion state to explicit tags that IndexTTS can use.
"""

from dataclasses import dataclass


@dataclass
class EmotionTagHint:
    """Hint for TTS system to shape voice delivery."""

    primary: str  # e.g., "playful", "caring", "cold", "annoyed"
    intensity: int  # 0-100
    secondary: str | None = None  # Optional secondary emotion


def vector_to_emotion_tags(
    affect_vector: dict[str, int],
    mood_state: str,
    relationship_score: int,
    max_tags: int = 3,
) -> list[EmotionTagHint]:
    """
    Convert Koroki's internal emotion vector to TTS-friendly emotion tags.

    Args:
        affect_vector: { "playfulness": int, "trust": int, "warmth": int, ...}
        mood_state: "baseline", "elevated", "reactive", "tired", "reflective"
        relationship_score: 0-100 relationship depth with user
        max_tags: max emotion tags to return

    Returns:
        List of EmotionTagHint objects ranked by intensity/relevance.
    """
    tags: list[EmotionTagHint] = []

    # Primary emotion from mood_state
    mood_primary = {
        "baseline": "neutral",
        "elevated": "playful",
        "reactive": "engaged",
        "tired": "soft",
        "reflective": "contemplative",
    }.get(mood_state, "neutral")

    # Extract and rank affect dimensions
    playfulness = affect_vector.get("playfulness", 0)
    trust = affect_vector.get("trust", 0)
    warmth = affect_vector.get("warmth", 0)
    irritation = affect_vector.get("irritation", 0)
    curiosity = affect_vector.get("curiosity", 0)
    fatigue = affect_vector.get("fatigue", 0)

    # High relationship = warmer tones; low = more reserved
    warmth_mod = int(warmth * (1 + (relationship_score / 100) * 0.3))

    # 1. Dominant emotional color
    if irritation > 60:
        tags.append(EmotionTagHint(primary="annoyed", intensity=min(irritation, 100), secondary="firm"))
    elif warmth_mod > 70 and trust > 60:
        tags.append(EmotionTagHint(primary="caring", intensity=warmth_mod, secondary="tender"))
    elif playfulness > 65:
        tags.append(EmotionTagHint(primary="playful", intensity=playfulness, secondary="teasing"))
    elif curiosity > 60:
        tags.append(EmotionTagHint(primary="curious", intensity=curiosity, secondary="engaged"))
    elif fatigue > 60:
        tags.append(EmotionTagHint(primary="tired", intensity=fatigue, secondary="soft"))
    else:
        tags.append(EmotionTagHint(primary=mood_primary, intensity=50, secondary=None))

    # 2. Secondary modulation (if room and significant)
    if len(tags) < max_tags:
        if trust > 70 and irritation < 40:
            tags.append(EmotionTagHint(primary="confident", intensity=trust, secondary=None))
        elif playfulness > 50 and fatigue < 50:
            tags.append(EmotionTagHint(primary="playful", intensity=playfulness, secondary=None))

    # 3. Relationship-specific flavor
    if len(tags) < max_tags and relationship_score >= 80:
        tags.append(EmotionTagHint(primary="affectionate", intensity=80, secondary=None))
    elif len(tags) < max_tags and relationship_score <= 20:
        tags.append(EmotionTagHint(primary="reserved", intensity=60, secondary=None))

    # Sort by intensity descending and trim
    tags = sorted(tags, key=lambda t: t.intensity, reverse=True)[:max_tags]

    return tags


def emotion_tags_to_prompt_hints(tags: list[EmotionTagHint]) -> list[str]:
    """
    Convert emotion tags to text hints for TTS or brain prompt injection.

    Returns list like: ["[emo:playful2]", "[emo:caring1]"]
    """
    hints = []
    for tag in tags:
        # Map intensity 0-100 to variant level 1-4
        level = max(1, min(4, (tag.intensity // 30) + 1))
        hints.append(f"[emo:{tag.primary}{level}]")
        if tag.secondary:
            secondary_level = max(1, min(3, (tag.intensity // 40) + 1))
            hints.append(f"[emo:{tag.secondary}{secondary_level}]")

    return hints[:4]  # Cap to 4 max tags in text
