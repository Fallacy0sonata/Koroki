"""
Maps Koroki's 10D affect vector → IndexTTS2 native emo_vector.

IndexTTS2 emo_vector order: [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]
Sum must be ≤ MAX_EMO_SUM; the remaining weight comes from the reference audio's natural voice.
"""

from __future__ import annotations

# How much of the total 1.0 emotion budget we consume at maximum intensity.
# At diffusion_steps=25, CFM applies emotion precisely — keep this low so the
# reference audio's natural timbre dominates and emotions are a subtle layer.
_MAX_EMO_SUM = 0.20

# Emotion dimension indices — kept as constants so code is readable without comments.
_HAPPY = 0
_ANGRY = 1
_SAD = 2
_AFRAID = 3
_DISGUSTED = 4
_MELANCHOLIC = 5
_SURPRISED = 6
_CALM = 7

# Per-label boosts concentrated on the appropriate emotion dims.
# These add on top of the continuous affect-vector signal.
_LABEL_BOOSTS: dict[str, list[float]] = {
    "neutral":        [0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.20],
    "caring":         [0.10, 0.00, 0.00, 0.00, 0.00, 0.05, 0.00, 0.05],
    "playful":        [0.15, 0.00, 0.00, 0.00, 0.00, 0.00, 0.05, 0.00],
    "protective":     [0.05, 0.00, 0.05, 0.00, 0.00, 0.00, 0.00, 0.10],
    "proud":          [0.10, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.05],
    "annoyed":        [0.00, 0.15, 0.00, 0.00, 0.05, 0.00, 0.00, 0.00],
    "frustrated":     [0.00, 0.20, 0.00, 0.00, 0.05, 0.00, 0.00, 0.00],
    "ragebaited":     [0.00, 0.25, 0.00, 0.00, 0.05, 0.00, 0.00, 0.00],
    "cold":           [0.00, 0.00, 0.05, 0.00, 0.05, 0.05, 0.00, 0.10],
    "tired":          [0.00, 0.00, 0.05, 0.00, 0.00, 0.10, 0.00, 0.05],
    "overstimulated": [0.00, 0.00, 0.00, 0.10, 0.00, 0.00, 0.10, 0.00],
}


def affect_to_emo_vector(
    affect_vector: dict[str, int],
    emotion_label: str,
    emotion_intensity: int,
    relationship_score: int = 50,
) -> list[float] | None:
    """
    Convert Koroki's internal affect state to IndexTTS2's emo_vector format.

    Returns None for neutral low-intensity states (reference audio drives tone naturally).
    Otherwise returns 8-float list [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]
    with sum bounded by MAX_EMO_SUM.
    """
    # For genuinely neutral speech, let the reference audio drive tone naturally.
    if emotion_label == "neutral" and emotion_intensity < 60:
        return None

    av = affect_vector

    def _f(key: str, default: int = 50) -> float:
        return av.get(key, default) / 100.0

    valence    = _f("valence")
    attachment = _f("attachment")
    irritation = _f("irritation")
    curiosity  = _f("curiosity")
    playful    = _f("playfulness")
    fatigue    = _f("fatigue")
    stress     = _f("stress")
    trust      = _f("trust")
    pride      = _f("pride")

    # Relationship score slightly amplifies warmth at high bonding levels.
    rel = relationship_score / 100.0
    warm_mod = attachment * (1.0 + rel * 0.2)

    raw: list[float] = [0.0] * 8

    # happy: high valence + playfulness + attachment, dampened by irritation
    raw[_HAPPY] = max(0.0, valence * 0.5 + playful * 0.3 + warm_mod * 0.15 - irritation * 0.25)

    # angry: high irritation + stress, dampened by valence
    raw[_ANGRY] = max(0.0, irritation * 0.55 + stress * 0.30 - valence * 0.20)

    # sad: low valence + fatigue, dampened by attachment
    raw[_SAD] = max(0.0, (1.0 - valence) * 0.40 + fatigue * 0.25 - attachment * 0.15)

    # afraid: stress × distrust interaction (both must be high)
    raw[_AFRAID] = max(0.0, stress * (1.0 - trust) * 0.55)

    # disgusted: irritation + distrust, dampened by attachment
    raw[_DISGUSTED] = max(0.0, irritation * 0.40 + (1.0 - trust) * 0.25 - attachment * 0.25)

    # melancholic: fatigue + low valence + attachment (wistful longing)
    raw[_MELANCHOLIC] = max(0.0, fatigue * 0.30 + (1.0 - valence) * 0.25 + attachment * 0.15 - playful * 0.15)

    # surprised: curiosity spike, dampened by fatigue
    raw[_SURPRISED] = max(0.0, curiosity * 0.55 - fatigue * 0.20)

    # calm: trust + low stress + low irritation (offset by 0.1 to avoid always dominating)
    raw[_CALM] = max(0.0, trust * 0.30 + (1.0 - stress) * 0.25 + (1.0 - irritation) * 0.20 - 0.10)

    # Apply per-label boosts
    boosts = _LABEL_BOOSTS.get(emotion_label, [0.0] * 8)
    raw = [max(0.0, r + b) for r, b in zip(raw, boosts)]

    total = sum(raw)
    if total < 1e-6:
        return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.05]

    # Scale so sum = intensity × MAX_EMO_SUM (minimum 0.05 to always provide some guidance)
    intensity_scale = max(0.0, min(1.0, emotion_intensity / 100.0))
    target_sum = max(0.05, intensity_scale * _MAX_EMO_SUM)

    return [round(r / total * target_sum, 4) for r in raw]
