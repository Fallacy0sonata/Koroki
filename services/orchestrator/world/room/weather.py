"""
Weather subsystem — outside her window.

Simple state machine over {clear, cloudy, rain, snow, storm}. Weather drifts
randomly over hours, biased by season (deferred: just a simple Markov for now).
Affects:
  - Ambient temperature (via ambient.py reading get_weather().current_state())
  - Eventual ambient sound (rain ASMR, wind, etc. — Phase 3 audio layer)
  - Lighting tint slightly (overcast → cooler color — deferred)
  - Felt-state context line

Transitions: each tick, small probability of state change. Realistic patterns:
  - clear → cloudy is more common than clear → snow
  - rain → cloudy is more common than rain → clear (rain ends slowly)
  - storm → rain is the typical exit

═══════════════════════════════════════════════════════════════════════════════
🐛 PREDICTED BUGS:

W1. "Weather changes way too fast / constantly different."
   Look at: TRANSITION_PROB_PER_HOUR — should be ~0.15 (one change per ~6 hours
   average). If you see weather flipping every few minutes, the per-tick math
   isn't scaled by elapsed time.

W2. "Weather stuck in snow during summer."
   Look at: _seasonal_bias() — Phase 2D MVP doesn't have seasonal logic, so
   snow can occur year-round. Phase 2E could add tilt by month.

W3. "After restart, weather resets to clear."
   Look at: persistence. State JSON should restore. If it doesn't,
   _STATE_PATH issue.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger("orchestrator.world.room.weather")

_REPO_ROOT = Path(__file__).resolve().parents[4]
_STATE_PATH = _REPO_ROOT / "data" / "world" / "weather_state.json"

# Average probability of a state transition per real-world hour.
TRANSITION_PROB_PER_HOUR = 0.15

# Transition weights: from_state → {to_state: weight}
# Higher weight = more likely transition target. Self-transitions excluded.
_TRANSITIONS: dict[str, dict[str, float]] = {
    "clear":   {"cloudy": 6.0, "rain": 1.0, "storm": 0.2, "snow": 0.3},
    "cloudy":  {"clear":  3.0, "rain": 3.0, "storm": 0.5, "snow": 1.0},
    "rain":    {"cloudy": 5.0, "storm": 1.5, "clear": 0.5},
    "storm":   {"rain":   6.0, "cloudy": 1.0},
    "snow":    {"cloudy": 4.0, "clear":  2.0, "rain": 0.5},
}

VALID_STATES = set(_TRANSITIONS.keys())


def _weighted_pick(options: dict[str, float]) -> str:
    total = sum(options.values())
    r = random.uniform(0, total)
    acc = 0.0
    for k, w in options.items():
        acc += w
        if r <= acc:
            return k
    return next(iter(options.keys()))  # fallback


@dataclass
class WeatherState:
    state: str = "clear"
    last_tick_ts: float = 0.0
    last_transition_ts: float = 0.0


class WeatherSystem:
    """Single-instance threadsafe outside-weather state machine."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._state = WeatherState(last_tick_ts=time.time())
        self._state_path = state_path or _STATE_PATH
        self._load()

    def tick(self, now_ts: float | None = None) -> None:
        """Maybe transition. Probability scales with elapsed time."""
        ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            dt = max(0.0, ts - self._state.last_tick_ts)
            self._state.last_tick_ts = ts
            if dt <= 0:
                return
            # Probability of transitioning in this dt
            p_transition = 1.0 - (1.0 - TRANSITION_PROB_PER_HOUR) ** (dt / 3600.0)
            if random.random() < p_transition:
                transitions = _TRANSITIONS.get(self._state.state, {})
                if transitions:
                    new_state = _weighted_pick(transitions)
                    logger.info("Weather: %s → %s (after %.0fs)",
                                self._state.state, new_state, ts - self._state.last_transition_ts)
                    self._state.state = new_state
                    self._state.last_transition_ts = ts

    def current_state(self) -> str:
        with self._lock:
            return self._state.state

    def set_state(self, new_state: str, now_ts: float | None = None) -> None:
        if new_state not in VALID_STATES:
            raise ValueError(f"unknown weather state: {new_state}")
        ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            self._state.state = new_state
            self._state.last_transition_ts = ts
            self._state.last_tick_ts = ts

    def contribute_to_felt_state(self, out: dict[str, list[str]]) -> None:
        s = self.current_state()
        fragments = {
            "clear":  "clear sky outside",
            "cloudy": "overcast outside",
            "rain":   "rain against the window",
            "storm":  "storm — rain and wind outside",
            "snow":   "snow drifting past the window",
        }
        if s in fragments:
            out["context"].append(fragments[s])

    # ─── Persistence ───
    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            s = data.get("state", "clear")
            if s in VALID_STATES:
                self._state.state = s
            self._state.last_tick_ts = float(data.get("last_tick_ts", time.time()))
            self._state.last_transition_ts = float(data.get("last_transition_ts", 0.0))
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

_INSTANCE: WeatherSystem | None = None
_INSTANCE_LOCK = threading.Lock()


def get_weather() -> WeatherSystem:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = WeatherSystem()
    return _INSTANCE
