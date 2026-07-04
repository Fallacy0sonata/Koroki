"""
Converts the nervous system state dict into a compact prompt block
injected into Koroki's system prompt as context.

The LLM never sees raw variable names or float values.
It sees a natural-language state description it can respond from.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .rumination import peek_surfaced_rumination

_UTC7 = timezone(timedelta(hours=7))


def _label(value: float, steps: list[tuple[float, str]]) -> str:
    """Map a continuous value to a label. steps is [(threshold, label), ...] ascending."""
    for threshold, label in steps:
        if value <= threshold:
            return label
    return steps[-1][1]


_ENERGY_LABELS = [
    (0.20, "very low"),
    (0.40, "low"),
    (0.58, "moderate"),
    (0.75, "good"),
    (1.00, "high"),
]

_COMFORT_LABELS = [
    (0.20, "uncomfortable"),
    (0.40, "uneasy"),
    (0.58, "neutral"),
    (0.75, "comfortable"),
    (1.00, "very comfortable"),
]

_OPENNESS_LABELS = [
    (0.25, "closed off"),
    (0.45, "guarded"),
    (0.60, "present"),
    (0.78, "open"),
    (1.00, "very open"),
]


def _mood_label(valence: float, arousal: float, sharpness: float) -> str:
    if valence >= 0.68 and arousal >= 0.65:
        return "playful" if sharpness >= 0.55 else "energized"
    if valence >= 0.68 and arousal < 0.45:
        return "content" if sharpness < 0.50 else "settled"
    if valence >= 0.55 and arousal >= 0.55:
        return "engaged"
    if valence < 0.38 and arousal >= 0.62:
        return "sharp" if sharpness >= 0.65 else "restless"
    if valence < 0.38 and arousal < 0.42:
        return "withdrawn" if sharpness < 0.40 else "quiet and edgy"
    if sharpness >= 0.72:
        return "sharp"
    if valence >= 0.55:
        return "present"
    return "quiet"


def _cognition_quality(hour_utc7: int) -> str:
    if 0 <= hour_utc7 <= 5:
        return "late night — thoughts drift deeper and stranger"
    elif 6 <= hour_utc7 <= 9:
        return "morning — concrete, slightly blunt, not fully warmed up"
    elif 10 <= hour_utc7 <= 17:
        return "afternoon — clear and social, sharpest right now"
    elif 18 <= hour_utc7 <= 21:
        return "evening — settled, warm, good for going deep"
    else:
        return "late evening — winding down, more introspective"


def build_state_block(state: dict[str, float]) -> str:
    """
    Returns a compact multi-line state block for system prompt injection.
    Looks like natural context, not a variable dump.
    """
    energy    = state.get("energy", 0.7)
    comfort   = state.get("comfort", 0.72)
    sb        = state.get("social_battery", 0.8)
    valence   = state.get("valence", 0.62)
    arousal   = state.get("arousal", 0.52)
    openness  = state.get("openness", 0.65)
    sharpness = state.get("sharpness", 0.48)
    spotlight_intensity = state.get("spotlight_intensity", 0.0)

    hour = datetime.now(_UTC7).hour
    mood = _mood_label(valence, arousal, sharpness)
    cognition = _cognition_quality(hour)

    energy_str   = _label(energy,   _ENERGY_LABELS)
    comfort_str  = _label(comfort,  _COMFORT_LABELS)
    openness_str = _label(openness, _OPENNESS_LABELS)
    battery_pct  = int(sb * 100)

    lines = [
        "## Current State",
        f"Energy: {energy_str}. Comfort: {comfort_str}. Social battery: {battery_pct}%.",
        f"Mood: {mood}. Openness: {openness_str}.",
        f"Time: {cognition}.",
    ]

    # Attention spotlight
    if spotlight_intensity >= 0.4:
        focus_label = "fully here" if spotlight_intensity >= 0.7 else "mostly here"
        lines.append(f"Attention: {focus_label} in this conversation.")

    # Surfaced rumination
    rumination = peek_surfaced_rumination()
    if rumination:
        content, _user_id = rumination
        lines.append(f"Background thought: {content}")

    return "\n".join(lines)
