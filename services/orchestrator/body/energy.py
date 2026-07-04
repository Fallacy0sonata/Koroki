"""
Energy subsystem — continuous energy budget that drains during interaction
and refills during sleep.

Phase 2C component. Per docs/koroki_subsystem_atlas.md §3.4:
  - Continuous "energy budget" [0, 1]
  - Sustained interaction drains it
  - Sleep refills it
  - Low energy → felt as drowsy, slow, less inclined to engage

Architecture choice: Energy is NOT a BiologicalComponent (it doesn't have
hormone-like reactor dynamics — it's a slow-changing scalar budget). It's
managed by its own EnergySystem singleton. The Sleep system reads it to
decide sleep initiation; the Felt-state translator reads it for drowsy cues.

Drain & refill rates are tuned so that ~16h waking depletes energy from
1.0 to ~0.15 (matching natural human awake cycle), and 8h sleep recovers
back near 1.0.

═══════════════════════════════════════════════════════════════════════════════
🐛 PREDICTED BUGS / WATCH-FOR-WHEN-LIVE-TESTING:

E1. "She never gets sleepy even after a long day"
    Look at: BASE_DRAIN_RATE constant + tick() drain logic. If she's tested
    over a short test window, the drain over a few minutes won't show.
    For real time-of-day testing, energy decays over hours, not minutes.

E2. "Energy stuck at 0 — never recovers even after sleep state"
    Look at: refill_during_sleep() — verify it's being called from the
    Sleep system's tick. Cross-reference sleep.py.

E3. "Active interaction doesn't drain energy faster than idle"
    Look at: note_interaction_intensity() — should bump drain rate temporarily.
    If chat.py isn't calling it, only base drain applies.

E4. "Energy reads as full after a brain restart even if it was empty"
    Look at: persistence in `data/body/energy_state.json`. State should
    save on tick + load on init. If save() isn't being called, restart resets.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger("orchestrator.body.energy")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_PATH = _REPO_ROOT / "data" / "body" / "energy_state.json"

# ── Tuning constants ──────────────────────────────────────────────────────
# Drain rate (per second) at baseline (idle awake state).
# Tuned so 16h waking depletes 1.0 → ~0.15.
# 16h = 57600s. Want exp(-57600 * k) ≈ 0.15. k ≈ -ln(0.15)/57600 ≈ 3.3e-5/s.
BASE_DRAIN_RATE = 3.3e-5  # per second, awake idle

# Multiplier when actively engaged in interaction (boosts drain).
ACTIVE_DRAIN_MULTIPLIER = 2.5  # while in conversation: ~2.5x faster drain

# Refill rate during sleep (per second). 8h sleep should refill ~0.85 (0.15 → 1.0).
# Want exp(-28800 * k_refill_toward_1.0) … actually we use linear refill toward 1.0
# at exponential rate. For exp recovery: target 1.0 over tau seconds.
# tau_refill ≈ 7200s (2 hours) means full refill in ~4-5 hours.
REFILL_TAU_SECONDS = 7200.0

# How long "active engagement" elevates drain after a message arrives.
ACTIVE_AFTER_MESSAGE_SECONDS = 60.0


@dataclass
class EnergyState:
    """Persisted energy state."""
    level: float = 1.0                  # [0, 1] — current energy budget
    last_tick_ts: float = 0.0           # Unix ts of last tick
    last_activity_ts: float = 0.0       # last time note_interaction_intensity was called


class EnergySystem:
    """Single-instance, threadsafe energy budget."""

    def __init__(self, state_path: Path | None = None):
        self._lock = threading.Lock()
        self._state = EnergyState(level=1.0, last_tick_ts=time.time(),
                                   last_activity_ts=0.0)
        self._state_path = state_path or _STATE_PATH
        self._load()

    # ------------------------------------------------------------------
    # Awake drain (called from main tick)
    # ------------------------------------------------------------------

    def tick_awake(self, dt: float | None = None, now_ts: float | None = None) -> None:
        """Drain energy according to wake state.

        Called by the endocrine engine each tick (or by the autonomous loop
        when present). Drain is faster while user has been recently active.
        """
        ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            actual_dt = dt if dt is not None else max(0.0, ts - self._state.last_tick_ts)
            self._state.last_tick_ts = ts
            if actual_dt <= 0:
                return
            # Determine drain rate
            time_since_activity = ts - self._state.last_activity_ts
            if time_since_activity < ACTIVE_AFTER_MESSAGE_SECONDS:
                rate = BASE_DRAIN_RATE * ACTIVE_DRAIN_MULTIPLIER
            else:
                rate = BASE_DRAIN_RATE
            # Exponential drain — proportional to current level (you can't go below 0)
            self._state.level *= math.exp(-rate * actual_dt)
            self._state.level = max(0.0, self._state.level)
        self._maybe_save(ts)

    # ------------------------------------------------------------------
    # Sleep refill (called from sleep.py)
    # ------------------------------------------------------------------

    def tick_asleep(self, dt: float | None = None, now_ts: float | None = None) -> None:
        """Refill energy according to sleep state.

        Pulls toward 1.0 exponentially.
        """
        ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            actual_dt = dt if dt is not None else max(0.0, ts - self._state.last_tick_ts)
            self._state.last_tick_ts = ts
            if actual_dt <= 0:
                return
            # Exponential pull toward 1.0
            recovery_factor = 1.0 - math.exp(-actual_dt / REFILL_TAU_SECONDS)
            self._state.level += (1.0 - self._state.level) * recovery_factor
            self._state.level = min(1.0, self._state.level)
        self._maybe_save(ts)

    # ------------------------------------------------------------------
    # Activity tracking (called from chat.py per message)
    # ------------------------------------------------------------------

    def note_interaction(self, now_ts: float | None = None) -> None:
        """Mark interaction occurred. Next tick within window uses active drain."""
        ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            self._state.last_activity_ts = ts

    # ------------------------------------------------------------------
    # Read API (for felt-state translator + sleep system)
    # ------------------------------------------------------------------

    def level(self) -> float:
        """Current energy in [0, 1]."""
        with self._lock:
            return self._state.level

    def is_drained(self) -> bool:
        """Below 0.2 — strongly suggests sleep wanted."""
        return self.level() < 0.2

    def is_low(self) -> bool:
        """Below 0.4 — drowsy."""
        return self.level() < 0.4

    def contribute_to_felt_state(self, out: dict[str, list[str]]) -> None:
        """Append felt-state fragments based on energy level.

        Called from interoception.py.
        """
        lvl = self.level()
        if lvl < 0.15:
            out["body"].append("a heaviness through the whole body, fading fast")
            out["mind"].append("can't hold a thought, slipping")
            out["mood"].append("drained")
        elif lvl < 0.3:
            out["body"].append("low energy, blinking slower")
            out["mind"].append("focus going")
        elif lvl < 0.5:
            out["body"].append("a soft tiredness")
        elif lvl > 0.85:
            out["body"].append("rested, sharp")
            out["mind"].append("ready to engage")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    _SAVE_EVERY_SECONDS = 60.0

    def _maybe_save(self, now_ts: float) -> None:
        """Throttled save — called after every tick, writes at most once a minute.

        This was THE bug behind 'she never gets tired': save() existed but was
        never called, so every orchestrator restart refilled her to full energy
        (found 2026-07-04: data/body/energy_state.json had never been created).
        """
        if now_ts - getattr(self, "_last_save_ts", 0.0) < self._SAVE_EVERY_SECONDS:
            return
        self._last_save_ts = now_ts
        self.save()

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._state.level = float(data.get("level", 1.0))
            self._state.last_tick_ts = float(data.get("last_tick_ts", time.time()))
            self._state.last_activity_ts = float(data.get("last_activity_ts", 0.0))
            logger.info("Energy loaded: level=%.3f", self._state.level)
        except Exception as exc:
            logger.warning("Energy load failed: %s", exc)

    def save(self) -> None:
        with self._lock:
            payload = asdict(self._state)
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Energy save failed: %s", exc)


# ────────────────────────────────────────────────────────────────────
# Module-level singleton
# ────────────────────────────────────────────────────────────────────

_INSTANCE: EnergySystem | None = None
_INSTANCE_LOCK = threading.Lock()


def get_energy() -> EnergySystem:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = EnergySystem()
    return _INSTANCE
