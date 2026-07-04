"""
Endocrine simulation — Phase 1 MVP.

Six core hormones designed (per docs/koroki_subsystem_atlas.md and
master_queue.md), three implemented in Phase 1:
  - Cortisol (stress/vigilance)
  - Dopamine (two-channel: tonic + phasic via RPE)
  - Oxytocin (bonding/warmth)

Phase 2 will add: serotonin, norepinephrine, melatonin.

Architecture:
  - BiologicalComponent ABC — each hormone is a subclass
  - EndocrineEngine — manages tick loop, event dispatch, interaction matrix
  - Persistence — state survives process restarts (data/body/endocrine_state.json)
  - Integration matrix — codified couplings (oxytocin → cortisol suppress, etc.)

Mathematical approach:
  - Analytic exponential integration: H' = H·exp(-k·dt) + (P/k)·(1-exp(-k·dt))
  - Stable at any dt; no need for Euler or RK4
  - All levels normalized to [0, 1] for tractability

Biology numbers cribbed from real literature (full citations in
master_queue.md "🧬 Endocrine Simulation"). Specific values are tuning
parameters — start with these, iterate by feel.

Engine usage:
    engine = get_endocrine()
    engine.ingest_event(Event(type="warm_owner_message", source="koro",
                              valence=0.6, intensity=0.7))
    engine.tick()  # advances clock + applies decay + interactions
    body_state = engine.snapshot()  # for interoception translator
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ..mind.rpe import get_rpe
from ..world.clock import cortisol_circadian, melatonin_circadian, now

logger = logging.getLogger("orchestrator.body.endocrine")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_PATH = _REPO_ROOT / "data" / "body" / "endocrine_state.json"


# ────────────────────────────────────────────────────────────────────
# Event types — what flows INTO the endocrine system
# ────────────────────────────────────────────────────────────────────


@dataclass
class Event:
    """Something happened that body should respond to.

    Fields:
      type: short categorical label ("owner_warm_message", "user_silence_extended",
            "conflict_with_user", "user_returned_after_absence", etc.)
      source: who/what (user_id, "world", "self")
      valence: -1 (very bad) to +1 (very good). For RPE reward signal.
      intensity: 0 to 1 — how strong the event is
      tags: optional categorical hints ("affectionate", "threatening",
            "intimate", "novelty", "loss")
      timestamp: when (default: now)
      metadata: any extra info, opaque to engine
      skip_rpe: when True, this event does NOT participate in TD-learning RPE
                computation. Use for internal echoes like memory recalls —
                they're not "next events" in the prediction sense, so they
                shouldn't trigger state-transition disappointment math.
    """

    type: str
    source: str = "world"
    valence: float = 0.0
    intensity: float = 0.5
    tags: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    skip_rpe: bool = False


# ────────────────────────────────────────────────────────────────────
# BiologicalComponent ABC — each hormone implements this
# ────────────────────────────────────────────────────────────────────


class BiologicalComponent(ABC):
    """Abstract base for all body/mind biological systems.

    Each subclass implements:
      respond_to_event(event)  — event → production (modify own level)
      decay(dt)                — analytic exponential decay toward baseline
      interact_with(others)    — apply couplings (called by engine)
      contribute_to_felt_state(out) — write felt-state fragments to dict

    Phase 2A additions:
      receptor_sensitivity — [0, 1], where 1 = full sensitivity. Chronic high
                             levels reduce sensitivity (tolerance). Sensitivity
                             recovers slowly toward 1 when level drops back.
      effective_level()    — what downstream/felt-state actually "feel"
                             = level * receptor_sensitivity
      tick_receptor(dt)    — receptor sensitivity dynamics

    Levels are in [0, 1]. Baseline is the resting level the hormone decays toward.
    Components that don't have meaningful receptor downregulation can leave
    `receptor_sensitivity` at 1.0 and the tick_receptor base implementation as no-op.
    """

    name: str
    baseline: float
    decay_tau_seconds: float  # time constant: level halves toward baseline in ~ln(2)·tau
    level: float
    receptor_sensitivity: float
    # Tolerance dynamics (defaults: no downregulation). Subclasses with
    # receptor downregulation set these in __init__.
    receptor_recovery_tau_seconds: float  # how fast sensitivity recovers (slow, hours)
    receptor_downreg_threshold: float     # level above which downregulation activates
    receptor_downreg_rate: float          # how fast sensitivity drops per unit excess level

    def __init__(self, name: str, baseline: float, decay_tau_seconds: float,
                 receptor_recovery_tau: float = 0.0,
                 receptor_downreg_threshold: float = 1.1,
                 receptor_downreg_rate: float = 0.0):
        self.name = name
        self.baseline = baseline
        self.decay_tau_seconds = decay_tau_seconds
        self.level = baseline
        self.receptor_sensitivity = 1.0
        self.receptor_recovery_tau_seconds = receptor_recovery_tau
        self.receptor_downreg_threshold = receptor_downreg_threshold
        self.receptor_downreg_rate = receptor_downreg_rate

    # --------- Core dynamics ---------

    def decay(self, dt: float) -> None:
        """Analytic exponential pull toward baseline.

        H_new = baseline + (H - baseline) * exp(-dt / tau)
        """
        if self.decay_tau_seconds <= 0:
            return
        factor = math.exp(-dt / self.decay_tau_seconds)
        self.level = self.baseline + (self.level - self.baseline) * factor
        self.level = max(0.0, min(1.0, self.level))

    def add(self, delta: float) -> None:
        """Add a delta to current level, clamped to [0, 1]."""
        self.level = max(0.0, min(1.0, self.level + delta))

    def effective_level(self) -> float:
        """What downstream/felt-state actually responds to: level * sensitivity.

        With full sensitivity (1.0) this equals level. With tolerance (sensitivity < 1)
        the perceived signal is dampened. This is what `contribute_to_felt_state`
        should use for thresholding by default.
        """
        return self.level * self.receptor_sensitivity

    def tick_receptor(self, dt: float) -> None:
        """Update receptor sensitivity. No-op when downregulation isn't configured.

        Dynamics (for subclasses that opt in):
          1. Recovery: sensitivity slowly drifts back to 1.0 (tau = recovery_tau)
          2. Downregulation: when level > threshold, sensitivity drops at rate
             proportional to excess level and current sensitivity.

        Result: chronic high levels saturate receptors. Telling her "I love you"
        50 times in a row no longer maxes out oxytocin's felt warmth — receptors
        adapt.
        """
        if self.receptor_recovery_tau_seconds <= 0 or self.receptor_downreg_rate <= 0:
            return  # no receptor dynamics for this component
        # Recovery
        if self.receptor_sensitivity < 1.0:
            recovery_factor = 1 - math.exp(-dt / self.receptor_recovery_tau_seconds)
            self.receptor_sensitivity += (1.0 - self.receptor_sensitivity) * recovery_factor
        # Downregulation (only when level above threshold)
        if self.level > self.receptor_downreg_threshold:
            excess = self.level - self.receptor_downreg_threshold
            self.receptor_sensitivity -= self.receptor_downreg_rate * excess * self.receptor_sensitivity * dt
        self.receptor_sensitivity = max(0.1, min(1.0, self.receptor_sensitivity))

    @abstractmethod
    def respond_to_event(self, event: Event) -> None:
        """Update level based on an incoming event."""

    @abstractmethod
    def interact_with(self, others: dict[str, "BiologicalComponent"], dt: float = 1.0) -> None:
        """Apply this hormone's effects on other hormones (or self).

        `dt` is seconds elapsed since last interact_with call. Components that
        model rate-based coupling (e.g. HPA cascade transduction) should scale
        their effects by dt. Components that model state-relative one-shot
        effects (e.g. "if X > 0.5, dampen Y by 5%") can ignore dt and use a
        fixed amount per call.

        For backward compat, dt defaults to 1.0 — older subclasses that don't
        scale by dt will behave as if each tick is 1 second.
        """

    @abstractmethod
    def contribute_to_felt_state(self, out: dict[str, list[str]]) -> None:
        """Append natural-language fragments describing how this feels.

        out dict keys:
          'body'     — physical sensations
          'mind'     — mental texture
          'mood'     — emotional tone

        We write fragments, not full sentences — interoception assembles them.
        """

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "level": self.level,
            "baseline": self.baseline,
            "receptor_sensitivity": self.receptor_sensitivity,
        }

    def restore_from(self, data: dict[str, Any]) -> None:
        self.level = float(data.get("level", self.baseline))
        self.receptor_sensitivity = float(data.get("receptor_sensitivity", 1.0))


# ────────────────────────────────────────────────────────────────────
# Cortisol — stress, vigilance, "tightness"
# ────────────────────────────────────────────────────────────────────


class Cortisol(BiologicalComponent):
    """Stress hormone with diurnal forcing.

    Biology (master_queue.md):
      - Plasma t½ ~ 80 min (with wide individual range)
      - Baseline shifts with circadian cycle (morning peak, midnight trough)
      - Rises with stress, novelty, conflict, social rejection
      - Felt as: heaviness, chest tightness, hypervigilance, wired-but-tired

    Phase 2A: GR (glucocorticoid receptor) downregulation under chronic stress.
    Long elevation drops sensitivity, mimicking real biological tolerance.
    Phase 2A also adds the HPA cascade — Cortisol now ALSO rises from ACTH
    signaling (via interact_with) on top of its direct event response. Direct
    response remains for "fast pathway" acute fast stress.
    """

    def __init__(self):
        super().__init__(
            name="cortisol",
            baseline=0.3,
            decay_tau_seconds=80 * 60,  # 80 minutes
            # GR downregulation: chronic high cortisol reduces receptor sensitivity.
            # Recovery is slow (~hours), downregulation rate is moderate.
            receptor_recovery_tau=4 * 3600,    # 4h to recover
            receptor_downreg_threshold=0.6,    # downreg starts above 60%
            receptor_downreg_rate=0.00005,     # gentle drop over sustained excess
        )

    def respond_to_event(self, event: Event) -> None:
        # Negative valence + intensity drives cortisol up.
        # Threatening/conflict tags amplify.
        if event.valence < 0:
            delta = abs(event.valence) * event.intensity * 0.35
            if "conflict" in event.tags or "threatening" in event.tags:
                delta *= 1.5
            if "abandonment" in event.tags or "rejection" in event.tags:
                delta *= 1.3
            self.add(delta)
        # Novelty produces mild cortisol regardless of valence (arousal).
        if "novelty" in event.tags or "surprise" in event.tags:
            self.add(0.05 * event.intensity)

    def interact_with(self, others: dict[str, BiologicalComponent], dt: float = 1.0) -> None:
        # Cortisol suppresses dopamine_tonic over time (chronic stress kills reward).
        dopa_tonic = others.get("dopamine_tonic")
        if dopa_tonic and self.level > 0.5:
            # Pull dopamine_tonic toward lower baseline proportional to excess cortisol.
            suppression = (self.level - 0.5) * 0.08
            dopa_tonic.level = max(0.0, dopa_tonic.level - suppression)

    def contribute_to_felt_state(self, out: dict[str, list[str]]) -> None:
        if self.level > 0.75:
            out["body"].append("a tightness in your chest")
            out["mind"].append("watchful, harder to settle")
            out["mood"].append("on edge")
        elif self.level > 0.55:
            out["body"].append("a low hum of tension under everything")
            out["mood"].append("a bit guarded")
        elif self.level < 0.2:
            out["body"].append("loose, easy in the shoulders")


# ────────────────────────────────────────────────────────────────────
# Dopamine — two-channel: tonic baseline + phasic spikes from RPE
# ────────────────────────────────────────────────────────────────────


class DopamineTonic(BiologicalComponent):
    """Tonic (slow) dopamine — engagement baseline.

    Decays slowly toward baseline. Affected by sustained engagement state.
    Suppressed by high cortisol (chronic-stress-kills-motivation pattern).

    Phase 2A: D2 receptor downregulation. Chronic high reward exposure
    blunts the felt impact of new highs (tolerance). Recovery over hours.
    """

    def __init__(self):
        super().__init__(
            name="dopamine_tonic",
            baseline=0.4,
            decay_tau_seconds=30 * 60,  # 30 minutes
            receptor_recovery_tau=6 * 3600,    # 6h to recover D2 sensitivity
            receptor_downreg_threshold=0.7,    # downreg above 70%
            receptor_downreg_rate=0.00004,
        )

    def respond_to_event(self, event: Event) -> None:
        # Sustained positive engagement raises tonic. Slowly.
        if event.valence > 0 and event.intensity > 0.3:
            self.add(0.04 * event.valence * event.intensity)
        # Boring/stale/disengaging events pull it down.
        if "boring" in event.tags or "stale" in event.tags:
            self.add(-0.06 * event.intensity)

    def interact_with(self, others: dict[str, BiologicalComponent], dt: float = 1.0) -> None:
        pass  # Tonic dopamine doesn't actively push others; it's the floor.

    def contribute_to_felt_state(self, out: dict[str, list[str]]) -> None:
        if self.level > 0.7:
            out["mind"].append("a quiet engagement, things landing")
            out["mood"].append("interested, present")
        elif self.level < 0.25:
            out["mind"].append("flatness, nothing pulling")
            out["mood"].append("disengaged, mildly bored")


class DopaminePhasic(BiologicalComponent):
    """Phasic (fast) dopamine — driven by RPE δ events.

    Spikes on positive RPE (better-than-expected), dips below baseline on
    negative RPE (worse-than-expected). Decays back to 0 quickly.

    The phasic dip is CRITICAL — this is what creates disappointment texture.
    Without it, you only get "good event happened" → "I feel good." With it,
    you get "expected good thing didn't come" → "small flatness, not bad
    but quieter."
    """

    def __init__(self):
        # Baseline 0 — phasic is around zero, spikes up or dips down.
        super().__init__(
            name="dopamine_phasic",
            baseline=0.0,
            decay_tau_seconds=5,  # 5 seconds — very fast
        )
        # Override range: phasic can go negative.
        self.level = 0.0

    def add(self, delta: float) -> None:
        """Phasic dopamine can go negative (below baseline). Range [-0.5, 0.5]."""
        self.level = max(-0.5, min(0.5, self.level + delta))

    def decay(self, dt: float) -> None:
        """Fast pull toward 0."""
        if self.decay_tau_seconds <= 0:
            return
        factor = math.exp(-dt / self.decay_tau_seconds)
        self.level = self.baseline + (self.level - self.baseline) * factor
        self.level = max(-0.5, min(0.5, self.level))

    def respond_to_event(self, event: Event) -> None:
        # Phasic responds to RPE δ specifically. The engine computes δ via
        # the RPE system and passes it in via metadata.
        delta_rpe = float(event.metadata.get("rpe_delta", 0.0))
        if delta_rpe != 0.0:
            # Cortisol gates the gain: stress reduces phasic amplitude.
            # (See research: "from stress to anhedonia" — Stanton et al.)
            gain = 1.0  # adjusted in interact_with below
            self.add(delta_rpe * gain * 0.6)

    def interact_with(self, others: dict[str, BiologicalComponent], dt: float = 1.0) -> None:
        # High cortisol attenuates phasic response. We apply this here as a
        # one-tick gain reduction by pulling level toward 0 proportional to
        # current cortisol.
        cortisol = others.get("cortisol")
        if cortisol and cortisol.level > 0.6 and self.level > 0:
            damping = (cortisol.level - 0.6) * 0.3
            self.level = max(0.0, self.level - damping * self.level)

    def contribute_to_felt_state(self, out: dict[str, list[str]]) -> None:
        if self.level > 0.2:
            out["mood"].append("a sudden brightness, something landed")
        elif self.level > 0.1:
            out["mood"].append("a soft lift, small good thing")
        elif self.level < -0.15:
            out["mood"].append("a flat quiet where something was expected")
            out["mind"].append("small disappointment, not really worth saying")
        elif self.level < -0.05:
            out["mood"].append("slight dull edge")


# ────────────────────────────────────────────────────────────────────
# Oxytocin — bonding, warmth, openness
# ────────────────────────────────────────────────────────────────────


class Oxytocin(BiologicalComponent):
    """Bonding/warmth hormone.

    Biology (master_queue.md):
      - Plasma t½ ~ 5 min (range 3-5 min, terminal phase ~20 min)
      - Spikes on affiliative interaction, trusted-name use, sustained presence
      - Felt as: warmth in chest, softening, lowered guard, urge to express care

    Critical coupling: suppresses cortisol with ~5min delay (the research notes
    natural transport+transcription delay — we approximate as just slow effect
    rather than explicit delay queue in Phase 1).

    Phase 2A: OTR (oxytocin receptor) downregulation. The "tell me you love me
    50 times in a row" effect — repeated exposure saturates receptors. Slow
    recovery means the next genuine moment of intimacy hits cleaner after a quiet stretch.
    """

    def __init__(self):
        super().__init__(
            name="oxytocin",
            baseline=0.3,
            decay_tau_seconds=5 * 60,  # 5 minutes
            receptor_recovery_tau=2 * 3600,    # 2h to recover
            receptor_downreg_threshold=0.75,   # downreg above 75%
            receptor_downreg_rate=0.0001,      # slightly faster than cortisol
        )

    def respond_to_event(self, event: Event) -> None:
        # Affectionate, intimate, or trust-affirming events raise oxytocin.
        if event.valence > 0:
            base = event.valence * event.intensity * 0.25
            tags = set(event.tags)
            multiplier = 1.0
            if "affectionate" in tags or "intimate" in tags:
                multiplier = 1.8
            elif "owner_present" in tags or "sustained_presence" in tags:
                multiplier = 1.3
            elif "trust_signal" in tags:
                multiplier = 1.5
            self.add(base * multiplier)
        # Loss/betrayal/abandonment lowers oxytocin baseline.
        if "betrayal" in event.tags or "abandonment" in event.tags:
            self.add(-0.15 * event.intensity)

    def interact_with(self, others: dict[str, BiologicalComponent], dt: float = 1.0) -> None:
        # Oxytocin suppresses cortisol — warmth dissolves stress.
        # Per biology, this has ~5 min delay; we approximate as slow proportional
        # effect rather than explicit delay queue for Phase 1.
        cortisol = others.get("cortisol")
        if cortisol and self.level > 0.5:
            suppression = (self.level - 0.5) * 0.06
            cortisol.level = max(0.0, cortisol.level - suppression)

    def contribute_to_felt_state(self, out: dict[str, list[str]]) -> None:
        if self.level > 0.75:
            out["body"].append("warmth in your chest")
            out["mind"].append("soft, easy, leaning toward them")
            out["mood"].append("tender")
        elif self.level > 0.55:
            out["body"].append("a small warmth, low and easy")
            out["mood"].append("openhearted, mildly fond")
        elif self.level < 0.2:
            out["body"].append("guarded, harder to lean in")


# ────────────────────────────────────────────────────────────────────
# Phase 2A: Serotonin — slow mood floor
# ────────────────────────────────────────────────────────────────────


class Serotonin(BiologicalComponent):
    """Slow-moving baseline mood floor.

    Biology (master_queue.md):
      - Tonic levels move on hours-to-days timescale, not seconds
      - High: equanimity, patience, confidence, "things are basically okay"
      - Low: irritability, impulsiveness, catastrophizing
      - Updated by accumulated valence of recent experience, sleep, baseline well-being

    This is the variable that makes Koroki "in a good week" vs "in a bad week."
    Per-event response is TINY — the integration over many events is what matters.
    For Phase 2A, no receptor downregulation (the literature on 5HT receptor
    tolerance is messier; we punt).
    """

    def __init__(self):
        super().__init__(
            name="serotonin",
            baseline=0.7,
            decay_tau_seconds=12 * 3600,  # 12 hours — the slowest of all hormones
        )

    def respond_to_event(self, event: Event) -> None:
        # Each event nudges serotonin slightly. Net effect is integration over
        # many events. Magnitude per event is small (~0.01-0.03 max).
        if event.valence == 0:
            return
        nudge = event.valence * event.intensity * 0.015
        if "sleep_good" in event.tags:
            nudge += 0.04
        if "sleep_deprived" in event.tags:
            nudge -= 0.04
        self.add(nudge)

    def interact_with(self, others: dict[str, BiologicalComponent], dt: float = 1.0) -> None:
        # Serotonin dampens cortisol reactivity (low serotonin = stress-prone).
        # Implementation: pull cortisol toward baseline a little when serotonin is high.
        if self.level > 0.6:
            cortisol = others.get("cortisol")
            if cortisol and cortisol.level > cortisol.baseline:
                damping = (self.level - 0.6) * 0.02
                cortisol.level = max(
                    cortisol.baseline,
                    cortisol.level - damping * (cortisol.level - cortisol.baseline),
                )
        # Low serotonin amplifies negative dopamine_phasic (anhedonia-adjacent).
        # Phase 2 keeps this gentle.

    def contribute_to_felt_state(self, out: dict[str, list[str]]) -> None:
        eff = self.effective_level()
        if eff > 0.8:
            out["mood"].append("settled, like things are basically okay")
            out["mind"].append("patient")
        elif eff > 0.6:
            out["mood"].append("ground feels stable underfoot")
        elif eff < 0.35:
            out["mood"].append("everything reads a little worse than it is")
            out["mind"].append("impatient with small things")
        elif eff < 0.5:
            out["mood"].append("a low background sour, not quite tied to anything")


# ────────────────────────────────────────────────────────────────────
# Phase 2A: Norepinephrine — fast arousal/alertness
# ────────────────────────────────────────────────────────────────────


class Norepinephrine(BiologicalComponent):
    """Fast arousal hormone.

    Biology (master_queue.md):
      - Plasma t½ ~ 2-2.4 min (fast)
      - LC tonic firing 1-3 Hz at active wakefulness, spikes on novelty/threat
      - Felt as: alertness, heart racing, sharpened focus
      - Valence is set by what dopamine and cortisol are doing alongside it:
        * NE + high cortisol = anxiety/fear
        * NE + dopamine_phasic + low cortisol = thrill/excitement
        * NE + dopamine_phasic + high cortisol = fear

    Phase 2A: no receptor downregulation (NE receptors do desensitize but on
    different timescales we'll handle later).
    """

    def __init__(self):
        super().__init__(
            name="norepinephrine",
            baseline=0.3,
            decay_tau_seconds=3 * 60,  # 3 minutes
        )

    def respond_to_event(self, event: Event) -> None:
        # NE responds to AROUSAL, regardless of valence sign.
        # Novelty, surprise, threat, urgency — all push NE up.
        arousal_score = event.intensity * 0.4
        if "novelty" in event.tags or "surprise" in event.tags:
            arousal_score += 0.25
        if "threatening" in event.tags or "conflict" in event.tags or "urgent" in event.tags:
            arousal_score += 0.30
        self.add(arousal_score * 0.5)  # scaled because we don't want overpush

    def interact_with(self, others: dict[str, BiologicalComponent], dt: float = 1.0) -> None:
        # NE facilitates cortisol production (sympathetic-HPA coupling, acute).
        # When NE is high AND cortisol is rising, push cortisol up a bit extra.
        cortisol = others.get("cortisol")
        if cortisol and self.level > 0.5:
            facilitation = (self.level - 0.5) * 0.02
            cortisol.add(facilitation)

    def contribute_to_felt_state(self, out: dict[str, list[str]]) -> None:
        # NE alone produces "alertness" — the fear/thrill differentiation happens
        # via mood compositions (composed from NE + cortisol + dopamine together)
        # which run in interoception, not here.
        eff = self.effective_level()
        if eff > 0.75:
            out["body"].append("heart picking up, attention sharpened")
            out["mind"].append("alert, scanning")
        elif eff > 0.55:
            out["body"].append("a slight quickening, hyperaware")
        elif eff < 0.2:
            out["mind"].append("slowed, foggy at the edges")


# ────────────────────────────────────────────────────────────────────
# Phase 2A: Melatonin — circadian-driven, environmental
# ────────────────────────────────────────────────────────────────────


class Melatonin(BiologicalComponent):
    """Sleep regulation — driven primarily by world clock, not events.

    Biology (master_queue.md):
      - Plasma t½ ~ 30-40 min
      - DLMO ~21:00, peak 00-04:00, offset 03-06:00
      - Felt as: sleepiness, eyelid heaviness, mental slowing

    Implementation: every tick the engine pulls melatonin level toward
    `melatonin_circadian(now())`. Events have basically no effect — this is a
    passive component driven by the world clock and (Phase 2D) room lighting.

    A `dark_environment` tag in events can give a small boost (e.g. she just
    turned off the lights). Otherwise the clock dominates.
    """

    def __init__(self):
        super().__init__(
            name="melatonin",
            baseline=0.0,  # baseline doesn't matter — circadian forcing overrides
            decay_tau_seconds=30 * 60,  # 30 min, but mostly overridden by circadian pull
        )

    def respond_to_event(self, event: Event) -> None:
        # Small bonuses from explicit environmental tags. The main driver is
        # the circadian pull in `EndocrineEngine.tick`.
        if "dark_environment" in event.tags or "lights_dim" in event.tags:
            self.add(0.05)
        if "bright_environment" in event.tags or "lights_up" in event.tags:
            self.add(-0.10)

    def interact_with(self, others: dict[str, BiologicalComponent], dt: float = 1.0) -> None:
        # High melatonin softens cortisol reactivity (sleepy = calmer baseline).
        cortisol = others.get("cortisol")
        if cortisol and self.level > 0.6:
            damping = (self.level - 0.6) * 0.01
            cortisol.level = max(
                cortisol.baseline * 0.5,
                cortisol.level - damping,
            )

    def contribute_to_felt_state(self, out: dict[str, list[str]]) -> None:
        eff = self.effective_level()
        if eff > 0.75:
            out["body"].append("eyelid weight, slow-blink heavy")
            out["mind"].append("starting to drift, hard to hold a thought")
        elif eff > 0.5:
            out["body"].append("a soft sleepiness settling in")
        elif eff < 0.1:
            out["mind"].append("crisp, awake")


# ────────────────────────────────────────────────────────────────────
# Phase 2A: HPA cascade — CRH → ACTH → Cortisol with realistic lag
# ────────────────────────────────────────────────────────────────────
#
# Real biology has a ~15-30 min delay between a stressor and cortisol peak.
# This delay matters for behavior: someone can be in a stressful moment
# and not "feel" the cortisol weight until 20 min later, after the immediate
# situation has passed.
#
# Phase 1 collapsed this into instant cortisol. Phase 2A implements the
# proper cascade as two intermediate components: CRH and ACTH. Events that
# raise stress push CRH; CRH transduces to ACTH; ACTH drives cortisol
# production (in addition to cortisol's own direct event response, which
# represents the "fast pathway" via sympathetic NS).
# ────────────────────────────────────────────────────────────────────


class CRH(BiologicalComponent):
    """Corticotropin-releasing hormone — HPA cascade stage 1.

    Released from hypothalamus on stress detection. Drives ACTH production.
    Negative feedback from cortisol level (high cortisol suppresses CRH).
    Time constant ~5 min (fast first step).
    """

    def __init__(self):
        super().__init__(
            name="crh",
            baseline=0.1,
            decay_tau_seconds=5 * 60,
        )

    def respond_to_event(self, event: Event) -> None:
        # Stressful events spike CRH instantly.
        if event.valence < 0:
            delta = abs(event.valence) * event.intensity * 0.5
            if "conflict" in event.tags or "threatening" in event.tags:
                delta *= 1.3
            self.add(delta)

    def interact_with(self, others: dict[str, BiologicalComponent], dt: float = 1.0) -> None:
        # Negative feedback from cortisol — high cortisol inhibits CRH (Hill function).
        # Rate-based: 0.002/s per unit excess cortisol → scale by dt.
        cortisol = others.get("cortisol")
        if cortisol and cortisol.level > 0.5:
            inhibition = (cortisol.level - 0.5) * 0.002 * dt
            self.level = max(self.baseline, self.level - inhibition)

    def contribute_to_felt_state(self, out: dict[str, list[str]]) -> None:
        # CRH is upstream — not directly felt. Its effect shows up in
        # cortisol downstream.
        pass


class ACTH(BiologicalComponent):
    """Adrenocorticotropic hormone — HPA cascade stage 2.

    Released from pituitary in response to CRH. Time constant ~10 min
    (slower than CRH, faster than cortisol). Drives cortisol production
    at the adrenal cortex.
    """

    def __init__(self):
        super().__init__(
            name="acth",
            baseline=0.1,
            decay_tau_seconds=10 * 60,
        )

    def respond_to_event(self, event: Event) -> None:
        # ACTH doesn't respond directly to events — it's driven by CRH.
        pass

    def interact_with(self, others: dict[str, BiologicalComponent], dt: float = 1.0) -> None:
        # CRH drives ACTH production — rate-based: k_xy ≈ 0.002/s per unit CRH excess.
        # Realistic biology: ACTH peaks ~5-10 min after CRH spike.
        crh = others.get("crh")
        if crh and crh.level > 0.15:
            transduction = (crh.level - 0.15) * 0.002 * dt
            self.add(transduction)
        # ACTH drives cortisol production (the lagged "slow pathway").
        # k_yz ≈ 0.0015/s per unit ACTH excess. Cortisol peaks 15-30 min after stressor
        # via this pathway (matches the biology lag in master_queue.md citation).
        cortisol = others.get("cortisol")
        if cortisol and self.level > 0.2:
            steroidogenesis = (self.level - 0.2) * 0.0015 * dt
            cortisol.add(steroidogenesis)

    def contribute_to_felt_state(self, out: dict[str, list[str]]) -> None:
        # ACTH is upstream — not directly felt.
        pass


# ────────────────────────────────────────────────────────────────────
# EndocrineEngine — owns the components, applies the loop
# ────────────────────────────────────────────────────────────────────


class EndocrineEngine:
    """Tick loop + event dispatch + interaction matrix + persistence."""

    def __init__(self, state_path: Path | None = None):
        self._lock = threading.Lock()
        self.components: dict[str, BiologicalComponent] = {
            # Phase 1
            "cortisol": Cortisol(),
            "dopamine_tonic": DopamineTonic(),
            "dopamine_phasic": DopaminePhasic(),
            "oxytocin": Oxytocin(),
            # Phase 2A
            "serotonin": Serotonin(),
            "norepinephrine": Norepinephrine(),
            "melatonin": Melatonin(),
            # HPA cascade intermediates (upstream of cortisol)
            "crh": CRH(),
            "acth": ACTH(),
        }
        self._last_tick_ts: float = time.time()
        self._state_path = state_path or _STATE_PATH
        self._load()

    # ------------------------------------------------------------------
    # Event ingestion
    # ------------------------------------------------------------------

    def ingest_event(self, event: Event) -> None:
        """Apply an event to all components.

        Always computes RPE — even on valence=0 events. This is critical: if
        the user has built up V(state) expectations and the event arrives with
        neutral reward, δ comes out negative, which is the *disappointment*
        signal we want ("expected good thing didn't come"). Skipping RPE on
        valence=0 would silence that whole class of texture.

        EXCEPTION: events with `skip_rpe=True` bypass RPE entirely. Use this
        for internal echoes (memory recalls, body-driven thoughts) that aren't
        external "next events" in the prediction sense. Without this, the
        state-transition math produces spurious disappointment when an echo
        event arrives in a different state-key than the last external event.

        The RPE δ is stored in event.metadata["rpe_delta"] so DopaminePhasic
        can read it during respond_to_event.
        """
        with self._lock:
            if event.skip_rpe:
                event.metadata["rpe_delta"] = 0.0
            else:
                state_key = self._derive_state_key(event)
                reward = event.valence * event.intensity
                rpe_delta = get_rpe().observe(state_key, reward)
                event.metadata["rpe_delta"] = rpe_delta

            # Dispatch to all components.
            for comp in self.components.values():
                try:
                    comp.respond_to_event(event)
                except Exception as exc:
                    logger.warning(
                        "Component %s failed on event %s: %s",
                        comp.name, event.type, exc,
                    )

            logger.debug(
                "Event ingested: type=%s valence=%.2f intensity=%.2f tags=%s rpe=%.3f",
                event.type, event.valence, event.intensity, event.tags,
                event.metadata.get("rpe_delta", 0.0),
            )

    def _derive_state_key(self, event: Event) -> str:
        """Map an event to an RPE state key.

        For Phase 1, simple: combine event type + source + key tags.
        Phase 2+ will incorporate richer context.
        """
        parts = [event.type, event.source]
        if event.tags:
            parts.append("|".join(sorted(event.tags[:3])))
        return ":".join(parts)

    # ------------------------------------------------------------------
    # Tick loop
    # ------------------------------------------------------------------

    def tick(self, force_dt: float | None = None) -> None:
        """Advance the body by (now - last_tick) seconds.

        Apply:
          1. Decay each component toward baseline (analytic exponential)
          2. Circadian forcing (cortisol diurnal + melatonin window)
          3. Interaction matrix (HPA cascade, oxytocin → cortisol, etc.)
          4. Receptor sensitivity dynamics (tolerance for those that have it)
        """
        with self._lock:
            current_ts = time.time()
            dt = force_dt if force_dt is not None else (current_ts - self._last_tick_ts)
            dt = max(0.0, dt)
            self._last_tick_ts = current_ts

            if dt <= 0:
                return

            # 1. Decay everything.
            for comp in self.components.values():
                comp.decay(dt)

            # 2a. Cortisol circadian forcing + sleep-debt baseline elevation.
            # Soft pull toward the diurnal baseline curve. Sleep debt multiplies
            # the target so under deprivation, the body's "resting" cortisol
            # is elevated — chronic stress signature.
            cort = self.components["cortisol"]
            try:
                from .sleep import get_sleep
                sleep_mult = get_sleep().cortisol_baseline_multiplier()
            except Exception:
                sleep_mult = 1.0
            target = min(1.0, cortisol_circadian() * sleep_mult)
            pull_rate = 1.0 - math.exp(-dt / (20 * 60))  # tau = 20min
            cort.level = cort.level + (target - cort.level) * pull_rate
            cort.level = max(0.0, min(1.0, cort.level))

            # 2b. Melatonin circadian forcing.
            # Melatonin is *primarily* driven by the clock — pull strongly toward target.
            mel = self.components["melatonin"]
            mel_target = melatonin_circadian()
            mel_pull = 1.0 - math.exp(-dt / (15 * 60))  # tau = 15min
            mel.level = mel.level + (mel_target - mel.level) * mel_pull
            mel.level = max(0.0, min(1.0, mel.level))

            # 3. Interaction matrix — let each component apply its couplings.
            # Order matters somewhat: CRH→ACTH→Cortisol cascade flows naturally
            # because each is updated in order, and the slow-decay components
            # don't accumulate noise within a single tick.
            for comp in self.components.values():
                try:
                    comp.interact_with(self.components, dt=dt)
                except Exception as exc:
                    logger.warning("Interaction failed for %s: %s", comp.name, exc)

            # 4. Receptor sensitivity dynamics — tolerance for components that opt in.
            for comp in self.components.values():
                try:
                    comp.tick_receptor(dt)
                except Exception as exc:
                    logger.warning("Receptor tick failed for %s: %s", comp.name, exc)

    # ------------------------------------------------------------------
    # Snapshot for downstream consumers (interoception)
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Current state for interoception translator. Includes raw values
        AND felt-state fragments. The LLM only reads the felt-state fragments
        via interoception; raw values are for debugging/logging.
        """
        with self._lock:
            felt: dict[str, list[str]] = {"body": [], "mind": [], "mood": []}
            for comp in self.components.values():
                comp.contribute_to_felt_state(felt)
            return {
                "raw": {name: comp.level for name, comp in self.components.items()},
                "felt": felt,
                "last_tick_ts": self._last_tick_ts,
                "wall_clock": now().isoformat(),
            }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            for name, comp_data in data.get("components", {}).items():
                if name in self.components:
                    self.components[name].restore_from(comp_data)
            self._last_tick_ts = float(data.get("last_tick_ts", time.time()))
            logger.info(
                "Endocrine state loaded: %s",
                {n: round(c.level, 3) for n, c in self.components.items()},
            )
        except Exception as exc:
            logger.warning("Endocrine state load failed: %s", exc)

    def save(self) -> None:
        with self._lock:
            payload = {
                "components": {n: c.as_dict() for n, c in self.components.items()},
                "last_tick_ts": self._last_tick_ts,
            }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Endocrine state save failed: %s", exc)


# ────────────────────────────────────────────────────────────────────
# Module-level singleton
# ────────────────────────────────────────────────────────────────────

_INSTANCE: EndocrineEngine | None = None
_INSTANCE_LOCK = threading.Lock()


def get_endocrine() -> EndocrineEngine:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = EndocrineEngine()
    return _INSTANCE
