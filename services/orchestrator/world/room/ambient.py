"""
Ambient subsystem — room temperature, humidity, and felt "room feel."

State:
  - temperature_c: degrees Celsius (15..30 reasonable range)
  - humidity: 0..1
  - User can adjust both; both drift back toward identity defaults over time
  - Outside weather affects temperature drift (winter → cooler, rain → cooler)

Felt-state contribution:
  - "cozy warmth" when temp 21-24°C
  - "a bit cool" when < 19°C
  - "stuffy/humid" when humidity > 0.7
  - "dry" when humidity < 0.3
  - These compose with body chemistry: cold + high cortisol → "hunched"
    (composition lives in mood_compositions.py — a Phase 2E follow-up could add
    these cross-system compositions; for Phase 2D we just contribute fragments)

═══════════════════════════════════════════════════════════════════════════════
🐛 PREDICTED BUGS:

A1. "Temperature never changes even when weather is cold outside."
   Look at: tick() — weather is read each tick via get_weather(). Verify weather
   subsystem is feeding back. Indoor temp drifts toward identity but
   weather_offset shifts that target. If weather is None or always "clear,"
   no shift happens.

A2. "Room is always reported as 'cool' regardless of actual temp."
   Look at: contribute_to_felt_state thresholds. The bands 19/21/24 may not
   match the user's intuition. Adjust if the felt-state lies.

A3. "After hours away, comes back to find the temp at exactly the identity
   default with no variation."
   Look at: DRIFT_TAU_SECONDS — should be long enough that real changes persist
   for the session but the room "remembers" its baseline overnight.
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

from .identity import IDENTITY_TEMP_DEFAULT, IDENTITY_HUMIDITY_DEFAULT

logger = logging.getLogger("orchestrator.world.room.ambient")

_REPO_ROOT = Path(__file__).resolve().parents[4]
_STATE_PATH = _REPO_ROOT / "data" / "world" / "ambient_state.json"

# How fast user overrides drift back toward identity baseline.
DRIFT_TAU_SECONDS = 6 * 3600  # 6 hours — physical thermal mass is slow

# Weather's effect on room temperature target (degrees C offset)
_WEATHER_TEMP_OFFSET = {
    "clear": 0.0,
    "cloudy": -0.5,
    "rain": -1.5,
    "snow": -3.0,
    "storm": -2.0,
}


@dataclass
class AmbientState:
    temperature_c: float = IDENTITY_TEMP_DEFAULT
    humidity: float = IDENTITY_HUMIDITY_DEFAULT
    last_tick_ts: float = 0.0


class AmbientSystem:
    """Single-instance threadsafe room ambient state."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._state = AmbientState(last_tick_ts=time.time())
        self._state_path = state_path or _STATE_PATH
        self._load()

    def tick(self, now_ts: float | None = None) -> None:
        """Drift toward identity defaults, shifted by current outside weather."""
        ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            dt = max(0.0, ts - self._state.last_tick_ts)
            self._state.last_tick_ts = ts
            if dt <= 0:
                return

            # Weather-adjusted temperature target
            weather_state = "clear"
            try:
                from .weather import get_weather
                weather_state = get_weather().current_state()
            except Exception:
                pass  # weather may not be imported yet
            temp_offset = _WEATHER_TEMP_OFFSET.get(weather_state, 0.0)
            temp_target = IDENTITY_TEMP_DEFAULT + temp_offset
            humidity_target = IDENTITY_HUMIDITY_DEFAULT
            if weather_state == "rain":
                humidity_target = 0.65
            elif weather_state == "snow":
                humidity_target = 0.35

            alpha = 1.0 - math.exp(-dt / DRIFT_TAU_SECONDS)
            self._state.temperature_c += (temp_target - self._state.temperature_c) * alpha
            self._state.humidity += (humidity_target - self._state.humidity) * alpha
            self._state.humidity = max(0.0, min(1.0, self._state.humidity))

    def set_temperature(self, temp_c: float, now_ts: float | None = None) -> None:
        ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            self._state.temperature_c = max(10.0, min(35.0, temp_c))
            self._state.last_tick_ts = ts

    def set_humidity(self, humidity: float, now_ts: float | None = None) -> None:
        ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            self._state.humidity = max(0.0, min(1.0, humidity))
            self._state.last_tick_ts = ts

    def temperature_c(self) -> float:
        with self._lock:
            return self._state.temperature_c

    def humidity(self) -> float:
        with self._lock:
            return self._state.humidity

    def contribute_to_felt_state(self, out: dict[str, list[str]]) -> None:
        """Append ambient-driven fragments to felt-state."""
        t = self.temperature_c()
        h = self.humidity()
        if t < 17:
            out["body"].append("a chill in the air")
            out["context"].append("noticeably cold room")
        elif t < 19:
            out["context"].append("room is a bit cool")
        elif t > 26:
            out["body"].append("warmth pressing in")
            out["context"].append("warm room")
        elif 21 <= t <= 24:
            out["context"].append("cozy room temperature")
        if h > 0.75:
            out["body"].append("air feels heavy, humid")
        elif h < 0.25:
            out["body"].append("air feels dry")

    # ─── Persistence ───
    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._state.temperature_c = float(data.get("temperature_c", IDENTITY_TEMP_DEFAULT))
            self._state.humidity = float(data.get("humidity", IDENTITY_HUMIDITY_DEFAULT))
            self._state.last_tick_ts = float(data.get("last_tick_ts", time.time()))
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

_INSTANCE: AmbientSystem | None = None
_INSTANCE_LOCK = threading.Lock()


def get_ambient() -> AmbientSystem:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = AmbientSystem()
    return _INSTANCE
