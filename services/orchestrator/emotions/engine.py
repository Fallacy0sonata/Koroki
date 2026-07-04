"""
Persistent emotional state engine for Koroki.

This keeps a lightweight but richer mood model than a single label.
Scalar fields remain for compatibility, but the internal state now tracks a
bounded affect vector so moods can drift slowly instead of snapping.

Core axes:
    - warmth: emotional closeness / tenderness
    - arousal: energetic vs subdued delivery
    - dominance: soft vs commanding posture
    - intensity: overall strength of the current mood

Vector axes:
    - valence, attachment, irritation, curiosity, playfulness, fatigue,
        stress, trust, pride, focus

The current emotion label is derived from those dimensions plus message cues.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
import re


Tone = Literal["positive", "neutral", "negative", "intimate", "playful", "protective"]

POSITIVE_WORDS = {
    "love", "glad", "happy", "beautiful", "wonderful", "thank", "appreciate",
    "cherish", "adore", "dear", "comfort", "warm", "gentle",
}
NEGATIVE_WORDS = {
    "hate", "angry", "annoyed", "frustrated", "furious", "sad", "hurt",
    "pain", "upset", "disappointed", "terrible", "awful", "horrible",
}
INTIMATE_WORDS = {
    "closer", "kiss", "touch", "hold", "embrace", "cuddle", "whisper",
    "miss you", "need you", "beside me",
}
PLAYFUL_WORDS = {
    "~", "tease", "smirk", "wink", "hehe", "play", "mischief", "giggle",
}
PROTECTIVE_WORDS = {
    "careful", "safe", "protect", "guard", "danger", "shield", "stay close",
}

GAME_WORDS = {
    "game", "match", "raid", "rank", "loss", "win", "queue", "pvp", "pve",
    "smurf", "lag", "lagging", "troll", "bait", "rage", "tilt", "feed",
    "throw", "trash", "clutch", "boss", "quest", "fight", "battle",
}

RAGEBAIT_WORDS = {
    "bait", "ragebait", "troll", "tilt", "trash", "toxic", "smurf", "throw",
    "feed", "lag", "grief", "salt", "annoying", "mock", "insult",
}

VECTOR_KEYS = (
    "valence",
    "attachment",
    "irritation",
    "curiosity",
    "playfulness",
    "fatigue",
    "stress",
    "trust",
    "pride",
    "focus",
)

# True neutral resting point for each affect dimension.
_NEUTRAL_BASELINE: dict[str, int] = {
    "valence": 50, "attachment": 50, "irritation": 20, "curiosity": 45,
    "playfulness": 35, "fatigue": 20, "stress": 20, "trust": 50, "pride": 40, "focus": 50,
}

# Per-step decay rates for the fast (spike) layer drifting toward slow mood.
# Irritation/stress recover fast; fatigue/attachment drift slower.
_FAST_DECAY: dict[str, float] = {
    "irritation": 0.16, "stress": 0.12, "fatigue": 0.08,
    "curiosity": 0.06,  "focus": 0.08,  "valence": 0.06,
    "playfulness": 0.07, "attachment": 0.04, "trust": 0.04, "pride": 0.05,
}

# Slow mood (baseline layer) drifts toward true neutral at this rate per step.
_SLOW_DRIFT: float = 0.015

# Fraction of each event's affect delta that bleeds into the slow mood layer.
_SLOW_INTEGRATION: float = 0.03


@dataclass
class RecentInteraction:
    timestamp: str
    tone: Tone
    nudge_amount: int


@dataclass
class EmotionalState:
    user_id: str
    base_emotion: str = "neutral"
    current_emotion: str = "neutral"
    intensity: int = 50
    warmth: int = 50
    arousal: int = 40
    dominance: int = 55
    affect_vector: dict[str, int] = field(default_factory=dict)
    # Slow mood layer — drifts on an hours-long timescale toward neutral.
    # Fast spikes in affect_vector decay toward this; sustained behavior shifts it.
    slow_mood: dict[str, int] = field(default_factory=dict)
    mood_state: str = "neutral"
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    recent_interactions: list[RecentInteraction] = field(default_factory=list)
    # Emotions felt during a nudge that didn't break through to flip the label —
    # minor irritation, guarded warmth, quiet frustration.  Accumulates across
    # sessions and surfaces in RAG facts once a pattern is detectable.
    private_feelings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.affect_vector:
            self.affect_vector = _default_affect_vector(
                warmth=self.warmth,
                arousal=self.arousal,
                dominance=self.dominance,
                intensity=self.intensity,
            )
        else:
            self.affect_vector = _normalize_affect_vector(self.affect_vector)
        # Slow mood seeds from affect_vector on first creation.
        if not self.slow_mood:
            self.slow_mood = dict(self.affect_vector)
        else:
            self.slow_mood = _normalize_affect_vector(self.slow_mood)
        self._sync_from_vector()

    def _sync_from_vector(self) -> None:
        self.valence = int(self.affect_vector.get("valence", 50))
        self.attachment = int(self.affect_vector.get("attachment", 50))
        self.irritation = int(self.affect_vector.get("irritation", 20))
        self.curiosity = int(self.affect_vector.get("curiosity", 45))
        self.playfulness = int(self.affect_vector.get("playfulness", 35))
        self.fatigue = int(self.affect_vector.get("fatigue", 20))
        self.stress = int(self.affect_vector.get("stress", 20))
        self.trust = int(self.affect_vector.get("trust", 50))
        self.pride = int(self.affect_vector.get("pride", 40))
        self.focus = int(self.affect_vector.get("focus", 50))
        self.warmth = int(self.affect_vector.get("attachment", self.warmth))
        self.arousal = int(100 - self.affect_vector.get("fatigue", 20) + self.affect_vector.get("playfulness", 35) // 3)
        self.dominance = int((self.affect_vector.get("trust", 50) + self.affect_vector.get("pride", 40)) / 2)
        self.intensity = int(
            max(
                self.affect_vector.get("stress", 20),
                self.affect_vector.get("irritation", 20),
                self.affect_vector.get("playfulness", 35),
                self.affect_vector.get("attachment", 50),
            )
        )
        self.mood_state = self.current_emotion

    def _sync_vector_from_scalars(self) -> None:
        defaults = _default_affect_vector(
            warmth=self.warmth,
            arousal=self.arousal,
            dominance=self.dominance,
            intensity=self.intensity,
        )
        for key in VECTOR_KEYS:
            if key not in self.affect_vector:
                self.affect_vector[key] = defaults.get(key, 50)
            else:
                self.affect_vector[key] = _clamp(self.affect_vector[key])
        self.affect_vector["attachment"] = max(self.affect_vector.get("attachment", self.warmth), self.warmth)
        self._sync_from_vector()

    def to_dict(self) -> dict:
        return {
            "base_emotion": self.base_emotion,
            "current_emotion": self.current_emotion,
            "mood_state": self.mood_state,
            "intensity": self.intensity,
            "warmth": self.warmth,
            "arousal": self.arousal,
            "dominance": self.dominance,
            "affect_vector": self.affect_vector,
            "slow_mood": self.slow_mood,
            "last_updated": self.last_updated,
            "recent_interactions": [
                {
                    "timestamp": item.timestamp,
                    "tone": item.tone,
                    "nudge_amount": item.nudge_amount,
                }
                for item in self.recent_interactions
            ],
            "private_feelings": list(self.private_feelings[-3:]),
        }

    @classmethod
    def from_dict(cls, user_id: str, data: dict | None) -> EmotionalState:
        if not data:
            return cls(user_id=user_id)

        interactions = [
            RecentInteraction(
                timestamp=item.get("timestamp", ""),
                tone=item.get("tone", "neutral"),
                nudge_amount=int(item.get("nudge_amount", 0)),
            )
            for item in data.get("recent_interactions", [])
        ]

        stored_av = _normalize_affect_vector(data.get("affect_vector") or {})
        # Fall back to affect_vector if slow_mood not yet in stored data (migration).
        stored_slow = _normalize_affect_vector(data.get("slow_mood") or stored_av)
        return cls(
            user_id=user_id,
            base_emotion=str(data.get("base_emotion", "neutral")),
            current_emotion=str(data.get("current_emotion", "neutral")),
            intensity=max(0, min(100, int(data.get("intensity", 50)))),
            warmth=max(0, min(100, int(data.get("warmth", 50)))),
            arousal=max(0, min(100, int(data.get("arousal", 40)))),
            dominance=max(0, min(100, int(data.get("dominance", 55)))),
            affect_vector=stored_av,
            slow_mood=stored_slow,
            mood_state=str(data.get("mood_state", data.get("current_emotion", "neutral"))),
            last_updated=str(data.get("last_updated", datetime.now(timezone.utc).isoformat())),
            recent_interactions=interactions[-6:],
            private_feelings=list(data.get("private_feelings") or [])[-3:],
        )


@dataclass(frozen=True)
class EmotionTTSHints:
    primary_tag: str
    secondary_tags: list[str]
    warmth: int
    arousal: int
    dominance: int
    intensity: int
    suggested_variant: str


def _clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(value)))


def _default_affect_vector(
    *,
    warmth: int,
    arousal: int,
    dominance: int,
    intensity: int,
) -> dict[str, int]:
    return {
        "valence": _clamp(50 + (warmth - 50) // 2 - max(0, 50 - intensity) // 4),
        "attachment": _clamp(warmth),
        "irritation": _clamp(20 + max(0, 50 - warmth) // 2),
        "curiosity": _clamp(45 + max(0, arousal - 40) // 2),
        "playfulness": _clamp(35 + max(0, arousal - 35) // 2),
        "fatigue": _clamp(100 - arousal),
        "stress": _clamp(20 + max(0, 55 - warmth) // 2 + max(0, 50 - dominance) // 4),
        "trust": _clamp(dominance),
        "pride": _clamp(dominance + max(0, warmth - 50) // 4),
        "focus": _clamp(50 + max(0, arousal - 40) // 3),
    }


def _normalize_affect_vector(vector: dict[str, Any]) -> dict[str, int]:
    base = _default_affect_vector(warmth=50, arousal=40, dominance=55, intensity=50)
    normalized: dict[str, int] = {}
    for key in VECTOR_KEYS:
        normalized[key] = _clamp(int(vector.get(key, base.get(key, 0))))
    return normalized


def _drift_toward_baseline(value: int, baseline: int, rate: float) -> int:
    return _clamp(round(value * (1.0 - rate) + baseline * rate))


def _apply_affect_delta(state: EmotionalState, deltas: dict[str, int]) -> None:
    for key, delta in deltas.items():
        state.affect_vector[key] = _clamp(state.affect_vector.get(key, 50) + int(delta))
    state._sync_from_vector()


def _message_features(message: str) -> dict[str, int]:
    lowered = re.sub(r"\s+", " ", message.lower()).strip()

    def _count(words: set[str]) -> int:
        score = 0
        for word in words:
            if word in lowered:
                score += 1
        return score

    return {
        "positive": _count(POSITIVE_WORDS),
        "negative": _count(NEGATIVE_WORDS),
        "intimate": _count(INTIMATE_WORDS),
        "playful": _count(PLAYFUL_WORDS),
        "protective": _count(PROTECTIVE_WORDS),
        "game": _count(GAME_WORDS),
        "ragebait": _count(RAGEBAIT_WORDS),
        "question": 1 if "?" in lowered else 0,
        "ellipsis": 1 if "..." in lowered else 0,
        "exclaim": 1 if "!" in lowered else 0,
    }


def _infer_message_tone(message: str) -> Tone:
    features = _message_features(message)
    if features["intimate"] > 0:
        return "intimate"
    if features["protective"] > 0:
        return "protective"
    if features["playful"] > 0:
        return "playful"
    if features["negative"] > features["positive"]:
        return "negative"
    if features["positive"] > features["negative"]:
        return "positive"
    return "neutral"


def _is_game_ragebait(message: str) -> bool:
    lowered = re.sub(r"\s+", " ", message.lower()).strip()
    return any(word in lowered for word in ("ragebait", "tilt", "trash", "troll", "smurf", "lag", "throw")) and any(
        word in lowered for word in ("game", "match", "raid", "rank", "queue", "pvp", "pve", "boss", "fight")
    )


def _derive_current_emotion(
    warmth: int,
    arousal: int,
    dominance: int,
    is_owner: bool,
    affect_vector: dict[str, int] | None = None,
) -> str:
    vector = affect_vector or {}
    irritation = int(vector.get("irritation", 20))
    stress = int(vector.get("stress", 20))
    playfulness = int(vector.get("playfulness", 35))
    fatigue = int(vector.get("fatigue", 20))
    attachment = int(vector.get("attachment", warmth))
    trust = int(vector.get("trust", dominance))

    if is_owner and warmth >= 58:
        return "caring"
    if irritation >= 72 and stress >= 58 and arousal >= 55:
        return "ragebaited"
    if stress >= 70 and irritation >= 60:
        return "overstimulated"
    if fatigue >= 72 and arousal <= 38:
        return "tired"
    if attachment >= 72 and warmth >= 70:
        return "caring"
    if playfulness >= 68 and arousal >= 55:
        return "playful"
    if warmth >= 72 and arousal <= 48:
        return "caring"
    if warmth >= 65 and arousal >= 55 and not is_owner:
        return "playful"
    if is_owner and warmth >= 54 and arousal >= 48:
        return "playful"
    if warmth >= 56 and dominance >= 62 and trust >= 52:
        return "protective"
    if arousal <= 28:
        return "tired"
    if warmth <= 28 and dominance >= 60:
        return "cold"
    if warmth <= 40 and arousal >= 62 and irritation >= 40:
        return "annoyed"
    if warmth <= 42 and arousal >= 52 and stress >= 30:
        return "frustrated"
    if warmth <= 45 and arousal >= 50 and irritation >= 30:
        return "annoyed"
    if arousal <= 36 and warmth <= 45:
        return "sad"
    if dominance >= 72 and warmth >= 50:
        return "proud"
    if is_owner and warmth >= 58:
        return "caring"
    return "neutral"


def compute_emotional_state(
    emotional_state: EmotionalState,
    relationship_score: int,
    is_owner: bool,
) -> EmotionalState:
    try:
        last_update = datetime.fromisoformat(emotional_state.last_updated)
        elapsed_seconds = max(0.0, (datetime.now(timezone.utc) - last_update).total_seconds())
    except (ValueError, TypeError):
        elapsed_seconds = 0.0

    decay_steps = min(6, int(elapsed_seconds // 120))
    if decay_steps > 0:
        for _ in range(decay_steps):
            # Scalar layer decays toward its own baseline (unchanged).
            emotional_state.warmth = _drift_toward_baseline(emotional_state.warmth, 50, 0.08)
            emotional_state.arousal = _drift_toward_baseline(emotional_state.arousal, 40, 0.1)
            emotional_state.dominance = _drift_toward_baseline(emotional_state.dominance, 55, 0.06)
            emotional_state.intensity = _drift_toward_baseline(emotional_state.intensity, 45, 0.1)
            # Fast affect layer drifts toward slow mood (not fixed baseline).
            for key in VECTOR_KEYS:
                rate = _FAST_DECAY.get(key, 0.07)
                mood_target = emotional_state.slow_mood.get(key, _NEUTRAL_BASELINE[key])
                emotional_state.affect_vector[key] = _drift_toward_baseline(
                    emotional_state.affect_vector.get(key, _NEUTRAL_BASELINE[key]),
                    mood_target,
                    rate,
                )
            # Slow mood drifts very gradually toward true neutral (hours timescale).
            for key in VECTOR_KEYS:
                emotional_state.slow_mood[key] = _drift_toward_baseline(
                    emotional_state.slow_mood.get(key, _NEUTRAL_BASELINE[key]),
                    _NEUTRAL_BASELINE[key],
                    _SLOW_DRIFT,
                )

    # On first ever compute (slow_mood still at neutral default), seed it from relationship context
    # so Koroki doesn't start every new user from identical neutral ground.
    _slow_is_default = all(
        abs(emotional_state.slow_mood.get(k, _NEUTRAL_BASELINE[k]) - _NEUTRAL_BASELINE[k]) <= 2
        for k in VECTOR_KEYS
    )
    if _slow_is_default:
        if is_owner:
            emotional_state.slow_mood["attachment"] = 68
            emotional_state.slow_mood["trust"] = 70
            emotional_state.slow_mood["valence"] = 62
            emotional_state.slow_mood["irritation"] = 14
        elif relationship_score < 30:
            emotional_state.slow_mood["trust"] = 32
            emotional_state.slow_mood["attachment"] = 38
            emotional_state.slow_mood["irritation"] = 28

    if is_owner:
        emotional_state.warmth = max(emotional_state.warmth, 62 if relationship_score >= 80 else 56)
        emotional_state.dominance = max(emotional_state.dominance, 52)
        emotional_state.affect_vector["attachment"] = max(emotional_state.affect_vector.get("attachment", 50), emotional_state.warmth)
        emotional_state.affect_vector["trust"] = max(emotional_state.affect_vector.get("trust", 50), 58)
    elif relationship_score < 35:
        emotional_state.warmth = min(emotional_state.warmth, 46)
        emotional_state.dominance = max(emotional_state.dominance, 58)
        emotional_state.affect_vector["trust"] = min(emotional_state.affect_vector.get("trust", 50), 54)

    emotional_state._sync_vector_from_scalars()

    emotional_state.current_emotion = _derive_current_emotion(
        emotional_state.warmth,
        emotional_state.arousal,
        emotional_state.dominance,
        is_owner=is_owner,
        affect_vector=emotional_state.affect_vector,
    )
    emotional_state.mood_state = emotional_state.current_emotion
    emotional_state.last_updated = datetime.now(timezone.utc).isoformat()
    return emotional_state


def _relationship_mood_scale(relationship_score: int, tone: str) -> float:
    """Relationship-based emotional sensitivity curve.

    Positive tones scale from 0.7x (stranger, score=0) up to 1.4x (close, score=100).
    Negative tones scale from 1.4x (stranger) down to 0.7x (close).
    Neutral tones are unscaled (1.0x) — no relationship bias on low-signal messages.

    Cap is 1.4x max amplification on either side. Combined with the global modifier_scale
    from mood_modifiers so the two systems multiply rather than compete.
    """
    norm = min(1.0, max(0.0, relationship_score / 100.0))
    if tone in ("positive", "intimate", "playful", "protective"):
        return round(0.7 + norm * 0.7, 3)
    if tone == "negative":
        return round(1.4 - norm * 0.7, 3)
    return 1.0


def nudge_emotion(
    emotional_state: EmotionalState,
    user_message: str,
    relationship_score: int,
    trust_level: int = 20,
    modifier_scale: float = 1.0,
) -> EmotionalState:
    _emotion_before = emotional_state.current_emotion
    tone = _infer_message_tone(user_message)
    modifier_scale = modifier_scale * _relationship_mood_scale(relationship_score, tone)
    features = _message_features(user_message)
    # Capture pre-nudge state so modifier scale can be retroactively applied.
    _vec_before = dict(emotional_state.affect_vector)
    _scalar_before = {
        "warmth": emotional_state.warmth,
        "arousal": emotional_state.arousal,
        "dominance": emotional_state.dominance,
        "intensity": emotional_state.intensity,
    }

    warmth_delta = 0
    arousal_delta = 0
    dominance_delta = 0
    intensity_delta = 0

    if tone == "positive":
        # Positive nudges are weaker than negative ones — negativity bias matches human psychology.
        warmth_delta += 7 if relationship_score >= 40 else 4
        arousal_delta += 3
        intensity_delta += 5
        emotional_state.affect_vector["valence"] = _clamp(emotional_state.affect_vector.get("valence", 50) + 6)
        emotional_state.affect_vector["attachment"] = _clamp(emotional_state.affect_vector.get("attachment", 50) + 3)
        emotional_state.affect_vector["trust"] = _clamp(emotional_state.affect_vector.get("trust", 50) + 2)
    elif tone == "negative":
        # Negative events hit ~1.5x harder than positive to match negativity bias.
        # High trust buffers the damage — you absorb rudeness better from someone you trust.
        _trust_buffer = max(0.0, (trust_level - 20) / 100.0)  # 0.0 at trust=20, 0.8 at trust=100
        _neg_warmth = 15 if relationship_score >= 40 else 20
        _neg_stress = 18 if relationship_score < 35 else 12
        _neg_irrit = 20 if relationship_score < 35 else 11
        warmth_delta -= round(_neg_warmth * (1.0 - _trust_buffer * 0.5))
        arousal_delta += 12
        dominance_delta += 5
        intensity_delta += 12
        emotional_state.affect_vector["valence"] = _clamp(emotional_state.affect_vector.get("valence", 50) - round(15 * (1.0 - _trust_buffer * 0.4)))
        emotional_state.affect_vector["stress"] = _clamp(emotional_state.affect_vector.get("stress", 20) + round(_neg_stress * (1.0 - _trust_buffer * 0.3)))
        emotional_state.affect_vector["irritation"] = _clamp(emotional_state.affect_vector.get("irritation", 20) + round(_neg_irrit * (1.0 - _trust_buffer * 0.3)))
    elif tone == "intimate":
        warmth_delta += 12
        arousal_delta += 5
        intensity_delta += 7
        emotional_state.affect_vector["attachment"] = _clamp(emotional_state.affect_vector.get("attachment", 50) + 12)
        emotional_state.affect_vector["trust"] = _clamp(emotional_state.affect_vector.get("trust", 50) + 8)
        emotional_state.affect_vector["valence"] = _clamp(emotional_state.affect_vector.get("valence", 50) + 5)
    elif tone == "playful":
        warmth_delta += 5
        arousal_delta += 10
        intensity_delta += 6
        emotional_state.affect_vector["playfulness"] = _clamp(emotional_state.affect_vector.get("playfulness", 35) + 12)
        emotional_state.affect_vector["curiosity"] = _clamp(emotional_state.affect_vector.get("curiosity", 45) + 4)
    elif tone == "protective":
        warmth_delta += 4
        dominance_delta += 10
        intensity_delta += 7
        emotional_state.affect_vector["stress"] = _clamp(emotional_state.affect_vector.get("stress", 20) + 5)
        emotional_state.affect_vector["trust"] = _clamp(emotional_state.affect_vector.get("trust", 50) + 4)

    if _is_game_ragebait(user_message):
        arousal_delta += 12
        dominance_delta -= 2
        intensity_delta += 10
        emotional_state.affect_vector["irritation"] = _clamp(emotional_state.affect_vector.get("irritation", 20) + 16)
        emotional_state.affect_vector["stress"] = _clamp(emotional_state.affect_vector.get("stress", 20) + 12)
        emotional_state.affect_vector["focus"] = _clamp(emotional_state.affect_vector.get("focus", 50) + 6)
        emotional_state.affect_vector["playfulness"] = _clamp(emotional_state.affect_vector.get("playfulness", 35) - 4)

    if features["question"]:
        arousal_delta += 2
    if features["ellipsis"]:
        arousal_delta -= 4
    if features["exclaim"]:
        arousal_delta += 4

    # Emotional inertia — a mood that's held for 3+ recent interactions develops momentum.
    # Opposing nudges are partially absorbed so Koroki can't be instantly snapped out of
    # a genuine groove. Mirrors how a truly happy person brushes off a single grumpy comment
    # and how a genuinely irritated person isn't instantly cheered up.
    _recent_tones = [ri.tone for ri in emotional_state.recent_interactions[-3:]]
    if len(_recent_tones) >= 3:
        _pos_streak = sum(1 for t in _recent_tones if t in ("positive", "intimate", "playful"))
        _neg_streak = sum(1 for t in _recent_tones if t == "negative")
        if _pos_streak >= 3 and tone == "negative":
            warmth_delta = round(warmth_delta * 0.55)
            arousal_delta = round(arousal_delta * 0.70)
            intensity_delta = round(intensity_delta * 0.75)
        elif _neg_streak >= 3 and tone in ("positive", "playful", "intimate"):
            warmth_delta = round(warmth_delta * 0.60)
            intensity_delta = round(intensity_delta * 0.65)

    # Apply modifier scale — retroactively scale all deltas from their pre-nudge baseline.
    # This means the same trigger produces different emotional movement depending on Koroki's
    # current condition (sick, tired, well-rested, time of day, etc.).
    if modifier_scale != 1.0:
        warmth_delta = round(warmth_delta * modifier_scale)
        arousal_delta = round(arousal_delta * modifier_scale)
        dominance_delta = round(dominance_delta * modifier_scale)
        intensity_delta = round(intensity_delta * modifier_scale)
        # Rescale inline vector mutations from their pre-nudge baseline
        for _key in VECTOR_KEYS:
            _before = _vec_before.get(_key, 50)
            _current = emotional_state.affect_vector.get(_key, _before)
            _delta = _current - _before
            if _delta != 0:
                emotional_state.affect_vector[_key] = _clamp(_before + round(_delta * modifier_scale))

    emotional_state.warmth = _clamp(_scalar_before["warmth"] + warmth_delta)
    emotional_state.arousal = _clamp(_scalar_before["arousal"] + arousal_delta)
    emotional_state.dominance = _clamp(_scalar_before["dominance"] + dominance_delta)
    emotional_state.intensity = _clamp(_scalar_before["intensity"] + intensity_delta, 20, 100)
    emotional_state._sync_vector_from_scalars()

    interaction = RecentInteraction(
        timestamp=datetime.now(timezone.utc).isoformat(),
        tone=tone,
        nudge_amount=intensity_delta,
    )
    emotional_state.recent_interactions.append(interaction)
    emotional_state.recent_interactions = emotional_state.recent_interactions[-6:]
    emotional_state.current_emotion = _derive_current_emotion(
        emotional_state.warmth,
        emotional_state.arousal,
        emotional_state.dominance,
        is_owner=relationship_score >= 80,
        affect_vector=emotional_state.affect_vector,
    )
    emotional_state.mood_state = emotional_state.current_emotion

    # Private feeling buffer: track emotions felt during this nudge that didn't
    # flip the label.  These represent things Koroki notices but doesn't show.
    # Accumulates across sessions; surfaces in RAG facts once a pattern forms.
    _emotion_after = emotional_state.current_emotion
    if _emotion_after == _emotion_before:
        if tone == "negative" and warmth_delta < -5:
            _suppressed = "mild_irritation" if warmth_delta > -10 else "quiet_frustration"
            _private = list(emotional_state.private_feelings or [])
            _private.append(_suppressed)
            emotional_state.private_feelings = _private[-3:]
        elif tone == "intimate" and _emotion_before in ("cold", "neutral", "tired"):
            _private = list(emotional_state.private_feelings or [])
            _private.append("guarded_warmth")
            emotional_state.private_feelings = _private[-3:]
    else:
        # Emotion label shifted — suppressed feeling has surfaced; clear the buffer.
        emotional_state.private_feelings = []

    # Bleed a tiny fraction of this event's fast-layer state into the slow mood.
    # Sustained patterns (many negative messages) gradually shift the baseline.
    for key in VECTOR_KEYS:
        fast_val = emotional_state.affect_vector.get(key, _NEUTRAL_BASELINE[key])
        slow_val = emotional_state.slow_mood.get(key, _NEUTRAL_BASELINE[key])
        emotional_state.slow_mood[key] = _clamp(round(slow_val + (fast_val - slow_val) * _SLOW_INTEGRATION))

    return emotional_state


def circadian_affect_overlay(now: datetime | None = None) -> dict[str, int]:
    """Return ephemeral affect deltas based on time of day (Borbély two-process model).

    Peaks alertness at 10am, troughs at ~10pm. Applied transiently at request
    time — never persisted to stored state, so the slow mood isn't contaminated.

    Caller adds these deltas to affect_vector when computing TTS/context output.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    hour = now.hour + now.minute / 60.0
    # Cosine oscillator: +1.0 at 10am, -1.0 at 10pm
    alertness = math.cos(2 * math.pi * (hour - 10) / 24)
    # Return raw signed deltas — caller clamps after adding to stored values.
    return {
        "fatigue":     round(-alertness * 15),   # alert → less tired (can be negative)
        "valence":     round(alertness * 8),      # alert → higher valence (can be negative)
        "focus":       round(alertness * 10),     # alert → sharper focus (can be negative)
        "playfulness": round(max(0.0, alertness) * 5),  # only boosted when alert, never penalised
    }


def circadian_energy_label(now: datetime | None = None) -> str:
    """Named energy tier from the same alertness oscillator used by circadian_affect_overlay.

    peak      8 am – 1 pm  (alertness >= 0.5)
    normal    1 pm – 6 pm  (alertness >= 0.0)
    low       6 pm – 10 pm (alertness >= -0.5)
    exhausted 10 pm – 5 am (alertness < -0.5)
    """
    if now is None:
        now = datetime.now(timezone.utc)
    hour = now.hour + now.minute / 60.0
    alertness = math.cos(2 * math.pi * (hour - 10) / 24)
    if alertness >= 0.5:
        return "peak"
    if alertness >= 0.0:
        return "normal"
    if alertness >= -0.5:
        return "low"
    return "exhausted"


def get_emotion_context_string(
    emotional_state: EmotionalState,
    is_owner: bool,
) -> str:
    recent = emotional_state.recent_interactions[-3:]
    recent_desc = "recent interactions were mixed"
    if recent:
        tones = [item.tone for item in recent]
        if tones.count("positive") + tones.count("intimate") > tones.count("negative"):
            recent_desc = "recent interactions have felt warm or affectionate"
        elif tones.count("negative") > tones.count("positive"):
            recent_desc = "a recent interaction left a trace of irritation"
        elif tones.count("playful") > 0:
            recent_desc = "there is a playful undercurrent in recent exchanges"

    owner_note = " (Speaking with creator — warmth is natural and appropriate)" if is_owner else ""

    av = emotional_state.affect_vector
    sm = emotional_state.slow_mood

    # Slow mood baseline tells Brain whether the current emotion is a spike or settled.
    slow_dominant = max(sm, key=lambda k: abs(sm.get(k, 50) - _NEUTRAL_BASELINE.get(k, 50)), default="valence")
    slow_val = sm.get(slow_dominant, 50)
    baseline_note = ""
    if slow_dominant == "irritation" and slow_val > 35:
        baseline_note = "; underlying baseline is slightly irritable"
    elif slow_dominant == "attachment" and slow_val > 60:
        baseline_note = "; underlying baseline is warm and attached"
    elif slow_dominant == "fatigue" and slow_val > 40:
        baseline_note = "; underlying baseline is tired"
    elif slow_dominant == "playfulness" and slow_val > 50:
        baseline_note = "; underlying baseline is playful"

    private_note = ""
    if emotional_state.private_feelings:
        private_note = f"\n- Private undercurrent (unexpressed): {', '.join(emotional_state.private_feelings[-2:])}"

    return (
        f"## Current Emotional Context\n"
        f"- Koroki is currently {emotional_state.current_emotion}"
        f" (mood: {emotional_state.mood_state}, intensity: {emotional_state.intensity}/100)"
        f"{owner_note}{baseline_note}\n"
        f"- Warmth={emotional_state.warmth}/100, Arousal={emotional_state.arousal}/100,"
        f" Dominance={emotional_state.dominance}/100\n"
        f"- Affect: valence={av.get('valence',50)} attachment={av.get('attachment',50)}"
        f" irritation={av.get('irritation',20)} stress={av.get('stress',20)}"
        f" playfulness={av.get('playfulness',35)} curiosity={av.get('curiosity',45)}"
        f" fatigue={av.get('fatigue',20)} trust={av.get('trust',50)}"
        f" pride={av.get('pride',40)} focus={av.get('focus',50)}\n"
        f"- {recent_desc}"
        f"{private_note}\n"
        f"- This colors her tone but does not override her core elegance"
    )


def get_tts_hints(
    emotional_state: EmotionalState,
    is_owner: bool,
    relationship_score: int,
) -> EmotionTTSHints:
    primary = emotional_state.current_emotion
    secondary: list[str] = []

    if emotional_state.affect_vector.get("irritation", 20) >= 68:
        secondary.append("firm")
    if emotional_state.affect_vector.get("stress", 20) >= 65:
        secondary.append("tense")
    if emotional_state.affect_vector.get("playfulness", 35) >= 66:
        secondary.append("playful")
    if emotional_state.affect_vector.get("attachment", 50) >= 70:
        secondary.append("caring")

    if emotional_state.warmth >= 72 and primary != "caring":
        secondary.append("caring")
    if emotional_state.arousal >= 62 and primary not in {"playful", "annoyed", "frustrated"}:
        secondary.append("playful" if emotional_state.warmth >= 45 else "firm")
    if emotional_state.arousal <= 30 and primary != "tired":
        secondary.append("tired")
    if emotional_state.dominance >= 68 and primary not in {"protective", "proud", "cold"}:
        secondary.append("proud" if emotional_state.warmth >= 50 else "cold")
    if is_owner and "caring" not in secondary and primary != "caring":
        secondary.append("caring")
    if relationship_score < 35 and "cold" not in secondary and primary == "neutral":
        secondary.append("cold")

    variant_map = {
        "neutral": "neutral_regal" if emotional_state.dominance >= 58 else "neutral_soft",
        "caring": "caring_tender" if emotional_state.arousal <= 45 else "caring_warm",
        "playful": "playful_teasing" if emotional_state.dominance >= 55 else "playful_light",
        "protective": "protective_firm" if emotional_state.dominance >= 64 else "protective_watchful",
        "whisper": "whisper_close",
        "sad": "sad_soft",
        "annoyed": "annoyed_cool" if emotional_state.dominance >= 58 else "annoyed_flat",
        "frustrated": "frustrated_tense",
        "ragebaited": "annoyed_flat",
        "overstimulated": "frustrated_tense",
        "cold": "cold_regal",
        "tired": "tired_soft",
        "proud": "proud_regal",
    }
    suggested_variant = variant_map.get(primary, "neutral_regal")

    deduped_secondary: list[str] = []
    seen: set[str] = {primary}
    for tag in secondary:
        if tag not in seen:
            deduped_secondary.append(tag)
            seen.add(tag)

    return EmotionTTSHints(
        primary_tag=primary,
        secondary_tags=deduped_secondary[:3],
        warmth=emotional_state.warmth,
        arousal=emotional_state.arousal,
        dominance=emotional_state.dominance,
        intensity=emotional_state.intensity,
        suggested_variant=suggested_variant,
    )
