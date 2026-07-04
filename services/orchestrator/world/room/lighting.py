"""
Lighting subsystem — room light level with circadian default + user override + drift.

Architecture:
  - Underlying value: light_level in [0, 1] (0=pitch dark, 1=fully bright)
  - Circadian target: follows a soft daylight curve (bright midday, low at night)
  - User can override: set_level(0.6) to brighten, set_level(0.2) to dim
  - Drift: without continued override, value slowly returns toward identity baseline
  - Feeds back into endocrine: low light → melatonin pressure (already wired
    via melatonin_circadian in clock.py; this module is the EXPLICIT room-side
    controller users can override)

Why separate from the existing melatonin_circadian:
  - clock.py's melatonin curve is a PURE TIME function — same every day
  - lighting.py represents the ACTUAL room light, which user/Koroki can change
  - Eventually melatonin should read lighting level, not just wall clock
  - For now Phase 2D ships the controller; the feedback wiring is a Phase 2E concern

Light level computation:
  effective_light = clamp(user_override OR circadian_default + drift_toward_identity)

═══════════════════════════════════════════════════════════════════════════════
🐛 PREDICTED BUGS:

L1. "Light never gets dim at night."
   Look at: _circadian_light_target() — should drop toward 0.1-0.2 between
   8pm-6am. If returning 0.5 at 2am, the curve is wrong.

L2. "User sets light to 0.8 and it stays bright forever even after going to sleep."
   Look at: tick() — should slowly drift overridden value back toward
   circadian default at DRIFT_TAU_SECONDS rate.

L3. "Light changes constantly even when user doesn't touch it."
   Look at: drift rate — should be SLOW (tau ~ hours). If user perceives
   constant change, tau is too short.

L4. "Bright daylight feels weird in her room."
   Look at: IDENTITY_LIGHT_DEFAULT in identity.py. Her room is canonically
   dim — drift target keeps it that way even at noon. If you want bright,
   user has to explicitly set it.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from ..clock import hour_of_day
from .identity import IDENTITY_LIGHT_DEFAULT

_REPO_ROOT = Path(__file__).resolve().parents[4]
_STATE_PATH = _REPO_ROOT / "data" / "world" / "lighting_state.json"

# ── Tuning constants ──────────────────────────────────────────────────────
# How fast user overrides drift back toward circadian default.
# Long tau (hours) — room reverts between sessions, not during them.
DRIFT_TAU_SECONDS = 4 * 3600  # 4 hours

# Bounds for the circadian default curve.
MIN_CIRCADIAN_LIGHT = 0.05  # deepest night
MAX_CIRCADIAN_LIGHT = 0.85  # brightest natural daylight in her room

# Late-night damping — even at "midday", her room is dim by default per
# her identity. We blend the circadian target with IDENTITY_LIGHT_DEFAULT.
IDENTITY_BLEND_WEIGHT = 0.5  # 0=fully circadian, 1=fully identity


def _circadian_light_target(hour: float) -> float:
    """Soft daylight curve. Bright midday, dim morning/evening, dark night.

    Returns 0.05..0.85. Sinusoidal-ish peak at 13:00.
    """
    # Peak at 13:00, trough at 1:00. Use cosine for smoothness.
    phase = (hour - 13.0) / 24.0 * 2 * math.pi
    raw = (math.cos(phase) + 1) / 2  # 0..1
    return MIN_CIRCADIAN_LIGHT + (MAX_CIRCADIAN_LIGHT - MIN_CIRCADIAN_LIGHT) * raw


def _identity_blended_target(hour: float) -> float:
    """Circadian curve blended with Koroki's identity default.

    Her room is canonically dim — even at 13:00 we don't fully brighten.
    """
    circadian = _circadian_light_target(hour)
    return (1 - IDENTITY_BLEND_WEIGHT) * circadian + IDENTITY_BLEND_WEIGHT * IDENTITY_LIGHT_DEFAULT


@dataclass
class LightingState:
    level: float = IDENTITY_LIGHT_DEFAULT  # current effective light
    last_tick_ts: float = 0.0
    last_user_override_ts: float = 0.0  # when the user last set it manually


class LightingSystem:
    """Single-instance, threadsafe room lighting state."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._state = LightingState(level=IDENTITY_LIGHT_DEFAULT, last_tick_ts=time.time())
        self._state_path = state_path or _STATE_PATH
        self._load()

    def tick(self, now_ts: float | None = None) -> None:
        """Advance: drift current level toward circadian-blended target."""
        ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            dt = max(0.0, ts - self._state.last_tick_ts)
            self._state.last_tick_ts = ts
            if dt <= 0:
                return
            target = _identity_blended_target(hour_of_day())
            # Exponential drift toward target
            alpha = 1.0 - math.exp(-dt / DRIFT_TAU_SECONDS)
            self._state.level += (target - self._state.level) * alpha
            self._state.level = max(0.0, min(1.0, self._state.level))

    def level(self) -> float:
        with self._lock:
            return self._state.level

    def set_level(self, level: float, now_ts: float | None = None) -> None:
        """User/Koroki sets light explicitly. Snaps current level + records timestamp."""
        ts = now_ts if now_ts is not None else time.time()
        level = max(0.0, min(1.0, level))
        with self._lock:
            self._state.level = level
            self._state.last_user_override_ts = ts
            self._state.last_tick_ts = ts

    def contribute_to_felt_state(self, out: dict[str, list[str]]) -> None:
        """Append felt-state fragments based on current light level."""
        lvl = self.level()
        if lvl < 0.15:
            out["context"].append("nearly dark, only screen glow")
        elif lvl < 0.35:
            out["context"].append("dim purple-tinted light")
        elif lvl < 0.6:
            pass  # canonical range, no specific fragment
        elif lvl < 0.85:
            out["context"].append("brighter than usual for her room")
        else:
            out["context"].append("daylight-level brightness in the room")

    # ─── Persistence ───
    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._state.level = float(data.get("level", IDENTITY_LIGHT_DEFAULT))
            self._state.last_tick_ts = float(data.get("last_tick_ts", time.time()))
            self._state.last_user_override_ts = float(data.get("last_user_override_ts", 0.0))
        except Exception:
            pass

    def save(self) -> None:
        with self._lock:
            payload = asdict(self._state)
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass


# ── Module-level singleton ────────────────────────────────────────────────

_INSTANCE: LightingSystem | None = None
_INSTANCE_LOCK = threading.Lock()


def get_lighting() -> LightingSystem:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = LightingSystem()
    return _INSTANCE
