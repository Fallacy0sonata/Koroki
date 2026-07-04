from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from typing import Any

from shared.utils.config import get_settings

_WORD_PATTERN = re.compile(r"\b\w+\b")
_INTENT_PATTERN = re.compile(
    r"\b(why|how|what|when|where|help|teach|explain|build|make|plan|should|could|can)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CognitiveSnapshot:
    observe_score: float
    affect_stability: float
    memory_coherence: float
    intent_strength: float
    attention_entropy: float
    initiative_drive: float
    reflection_score: float
    proactive_eligible: bool
    proactive_cooldown_s: int

    @property
    def coherence_score(self) -> float:
        # Keep this weighted blend stable and interpretable as a 0..1 measure.
        return max(
            0.0,
            min(
                1.0,
                (
                    self.observe_score * 0.16
                    + self.affect_stability * 0.2
                    + self.memory_coherence * 0.24
                    + self.intent_strength * 0.2
                    + self.reflection_score * 0.2
                ),
            ),
        )

    def to_runtime_facts(self) -> list[str]:
        return [
            "## Cognitive Context",
            f"cognitive_observe_score={self.observe_score:.3f}",
            f"cognitive_affect_stability={self.affect_stability:.3f}",
            f"cognitive_memory_coherence={self.memory_coherence:.3f}",
            f"cognitive_intent_strength={self.intent_strength:.3f}",
            f"cognitive_attention_entropy={self.attention_entropy:.3f}",
            f"cognitive_reflection_score={self.reflection_score:.3f}",
            f"cognitive_coherence_score={self.coherence_score:.3f}",
            f"cognitive_initiative_drive={self.initiative_drive:.3f}",
            f"cognitive_proactive_eligible={str(self.proactive_eligible).lower()}",
            f"cognitive_proactive_cooldown_s={self.proactive_cooldown_s}",
        ]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _entropy(words: list[str]) -> float:
    if not words:
        return 0.0
    counts: dict[str, int] = {}
    for word in words:
        counts[word] = counts.get(word, 0) + 1
    total = float(len(words))
    entropy = 0.0
    for count in counts.values():
        p = count / total
        entropy -= p * math.log(p, 2)
    # Normalize by a practical cap; 4 bits already indicates spread conversation focus.
    return _clamp01(entropy / 4.0)


def run_cognitive_cycle(
    *,
    user_id: str,
    user_message: str,
    merged_context: dict[str, Any],
    emotional_state: dict[str, Any] | None = None,
) -> CognitiveSnapshot:
    settings = get_settings()
    cog_cfg = settings.get("cognition", {})
    proactive_cfg = cog_cfg.get("proactive", {})

    words = [w.lower() for w in _WORD_PATTERN.findall(user_message)]
    word_count = len(words)
    question_bonus = 0.12 if "?" in user_message else 0.0
    exclaim_bonus = 0.06 if "!" in user_message else 0.0
    intent_hits = len(_INTENT_PATTERN.findall(user_message))

    observe_score = _clamp01(min(1.0, (word_count / 22.0)) + question_bonus + exclaim_bonus)

    recent_turns = list(merged_context.get("recent_turns") or [])
    core_facts = list(merged_context.get("core_facts") or [])
    memory_coherence = _clamp01(min(len(recent_turns), 8) / 8.0 * 0.58 + min(len(core_facts), 20) / 20.0 * 0.42)

    relationship_score = int(merged_context.get("relationship_score", 0))
    warmth = int((emotional_state or {}).get("warmth", 50))
    arousal = int((emotional_state or {}).get("arousal", 40))
    intensity = int((emotional_state or {}).get("intensity", 50))
    affect_alignment = 1.0 - abs(warmth - max(35, relationship_score)) / 100.0
    arousal_band = 1.0 - abs(arousal - 48) / 100.0
    intensity_band = 1.0 - abs(intensity - 55) / 100.0
    affect_stability = _clamp01(affect_alignment * 0.5 + arousal_band * 0.25 + intensity_band * 0.25)

    intent_strength = _clamp01(min(1.0, intent_hits / 3.0) * 0.72 + question_bonus * 0.7)
    attention_entropy = _entropy(words)

    # Deterministic jitter per user+message keeps initiative non-static without randomness sources.
    digest = hashlib.sha1(f"{user_id}:{user_message}".encode("utf-8", errors="ignore")).hexdigest()
    jitter = int(digest[:2], 16) / 255.0
    base_drive = float(proactive_cfg.get("base_drive", 0.35))
    initiative_drive = _clamp01(
        base_drive * 0.5
        + observe_score * 0.18
        + affect_stability * 0.2
        + (relationship_score / 100.0) * 0.12
        + jitter * 0.1
    )

    reflection_score = _clamp01((memory_coherence * 0.48) + (1.0 - attention_entropy) * 0.24 + intent_strength * 0.28)

    min_interval_s = max(30, int(proactive_cfg.get("min_interval_s", 600)))
    max_interval_s = max(min_interval_s, int(proactive_cfg.get("max_interval_s", 3600)))
    proactive_enabled = bool(proactive_cfg.get("enabled", False)) and bool(cog_cfg.get("enabled", True))
    proactive_eligible = proactive_enabled and initiative_drive >= float(proactive_cfg.get("eligibility_threshold", 0.62))
    # Higher drive means shorter cooldown, mapped safely into configured interval.
    cooldown_span = max_interval_s - min_interval_s
    proactive_cooldown_s = int(max_interval_s - (initiative_drive * cooldown_span))

    return CognitiveSnapshot(
        observe_score=observe_score,
        affect_stability=affect_stability,
        memory_coherence=memory_coherence,
        intent_strength=intent_strength,
        attention_entropy=attention_entropy,
        initiative_drive=initiative_drive,
        reflection_score=reflection_score,
        proactive_eligible=proactive_eligible,
        proactive_cooldown_s=proactive_cooldown_s,
    )
