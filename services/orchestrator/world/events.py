"""World events — discrete happenings in her environment that CAUSE hormone responses.

The causal chain (the design docs) made literal: before this module her world had continuous
state (weather, time, lighting) but nothing ever HAPPENED. Now a thunderclap spikes her
norepinephrine, a bird on the windowsill nudges dopamine, neighbor noise grates cortisol —
and everything downstream (felt state, thoughts, journal, conversation) inherits the
texture for free, because the hormones carry it.

Occurrence is random — that's the environment's job; randomness of WEATHER is not
randomness of EMOTION. The response is strictly causal: event → endocrine.ingest_event()
→ felt state. Events also land in the journal (her day genuinely contained them) and in
a short-lived "recent" buffer that the felt-state context line reads ("just now, a
thunderclap rattled the window").

Eligibility per event: weather / local-hour band / awake state / cooldown. Cooldowns
persist across restarts (data/world/events_state.json).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import clock

logger = logging.getLogger("orchestrator.world.events")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_PATH = _REPO_ROOT / "data" / "world" / "events_state.json"

TICK_SECONDS = 60.0
RECENT_WINDOW_S = 300.0  # how long an event colors the felt-state context


@dataclass(frozen=True)
class WorldEventDef:
    name: str
    texts: tuple[str, ...]            # one is picked per occurrence
    valence: float                    # -1..+1 → endocrine
    intensity: tuple[float, float]    # sampled range
    tags: tuple[str, ...] = ()
    rate_per_hour: float = 0.1        # expected occurrences/hour while eligible
    cooldown_s: float = 3600.0
    hours: tuple[int, int] = (0, 24)  # local-hour band, end may exceed 24 to wrap
    weather_any: tuple[str, ...] = () # eligible only if weather contains one of these
    weather_none: tuple[str, ...] = ()  # ineligible if weather contains one of these
    awake_only: bool = True


CATALOG: list[WorldEventDef] = [
    WorldEventDef(
        "thunderclap",
        ("a thunderclap rattled the window", "thunder rolled right over the building"),
        valence=-0.15, intensity=(0.5, 0.85), tags=("surprise", "urgent"),
        rate_per_hour=1.2, cooldown_s=900, weather_any=("storm", "thunder"),
        awake_only=False,  # thunder doesn't care that she's asleep
    ),
    WorldEventDef(
        "sunset_glow",
        ("the sunset went molten gold across the towers",
         "the whole skyline caught the sunset for a minute"),
        valence=0.4, intensity=(0.4, 0.6), tags=("novelty",),
        rate_per_hour=1.5, cooldown_s=20 * 3600, hours=(17, 19), weather_none=("rain", "storm"),
    ),
    WorldEventDef(
        "moon_through_clouds",
        ("the moon slid out from behind the clouds", "clear moonlight fell across the floor"),
        valence=0.2, intensity=(0.3, 0.5), tags=(),
        rate_per_hour=0.3, cooldown_s=8 * 3600, hours=(21, 26), weather_none=("rain", "storm"),
    ),
    WorldEventDef(
        "bird_on_sill",
        ("a small bird landed on the windowsill and looked in",
         "a sparrow hopped along the window ledge"),
        valence=0.35, intensity=(0.3, 0.5), tags=("novelty", "surprise"),
        rate_per_hour=0.15, cooldown_s=3 * 3600, hours=(7, 17), weather_none=("storm",),
    ),
    WorldEventDef(
        "neighbor_noise",
        ("the neighbors are moving furniture again, by the sound of it",
         "a muffled bass line started thudding through the wall"),
        valence=-0.2, intensity=(0.25, 0.45), tags=(),
        rate_per_hour=0.12, cooldown_s=4 * 3600, hours=(9, 22),
    ),
    WorldEventDef(
        "distant_siren",
        ("a siren wailed somewhere far below and faded",),
        valence=-0.1, intensity=(0.2, 0.35), tags=("urgent",),
        rate_per_hour=0.08, cooldown_s=6 * 3600,
    ),
    WorldEventDef(
        "power_flicker",
        ("the lights dipped for a heartbeat — the whole room blinked",),
        valence=-0.25, intensity=(0.5, 0.7), tags=("surprise", "urgent", "lights_dim"),
        rate_per_hour=0.02, cooldown_s=48 * 3600,
    ),
    WorldEventDef(
        "elevator_ding",
        ("the elevator dinged on her floor; footsteps passed and faded",),
        valence=0.05, intensity=(0.15, 0.25), tags=("novelty",),
        rate_per_hour=0.1, cooldown_s=5 * 3600, hours=(8, 22),
    ),
    WorldEventDef(
        "cooking_smell",
        ("someone's cooking drifted in from the corridor — garlic and something sweet",),
        valence=0.3, intensity=(0.3, 0.5), tags=("novelty",),
        rate_per_hour=0.15, cooldown_s=8 * 3600, hours=(11, 20),
    ),
]

# weather TRANSITIONS are events too (rain arriving is a happening, not just a state)
_WEATHER_SHIFTS = {
    "rain": ("rain started ticking against the glass", 0.1, ("novelty",)),
    "storm": ("the sky went heavy — a storm is rolling in", -0.2, ("threatening",)),
    "snow": ("it started snowing over the city", 0.35, ("novelty",)),
    "clear": ("the sky cleared up", 0.15, ()),
}


def _hour_in_band(h: float, band: tuple[int, int]) -> bool:
    a, b = band
    return a <= h <= b or a <= h + 24 <= b


class WorldEventEngine:
    def __init__(self, state_path: Path | None = None, rng: random.Random | None = None):
        self._lock = threading.Lock()
        self._state_path = state_path or _STATE_PATH
        self._rng = rng or random.Random()
        self._last_fired: dict[str, float] = {}
        self._recent: list[dict] = []  # {ts, name, text}
        self._last_weather: str | None = None
        self._load()

    # ------------------------------------------------------------------

    def tick(self, dt: float = TICK_SECONDS) -> list[dict]:
        """Advance one tick; returns any events fired (for tests/telemetry)."""
        fired: list[dict] = []
        now = time.time()
        weather = self._safe_weather()
        h = clock.hour_of_day()
        awake = self._safe_awake()

        # weather transitions
        if self._last_weather is not None and weather != self._last_weather:
            shift = _WEATHER_SHIFTS.get(self._split_key(weather))
            if shift:
                text, valence, tags = shift
                fired.append(self._fire(f"weather_{self._split_key(weather)}", text,
                                        valence, 0.4, tags, now))
        self._last_weather = weather

        for ev in CATALOG:
            if ev.awake_only and not awake:
                continue
            if not _hour_in_band(h, ev.hours):
                continue
            if ev.weather_any and not any(w in weather for w in ev.weather_any):
                continue
            if ev.weather_none and any(w in weather for w in ev.weather_none):
                continue
            if now - self._last_fired.get(ev.name, 0.0) < ev.cooldown_s:
                continue
            p = ev.rate_per_hour * dt / 3600.0
            if self._rng.random() >= p:
                continue
            text = self._rng.choice(ev.texts)
            intensity = self._rng.uniform(*ev.intensity)
            fired.append(self._fire(ev.name, text, ev.valence, intensity, ev.tags, now))

        if fired:
            self._save()
        return fired

    def _fire(self, name: str, text: str, valence: float, intensity: float,
              tags: tuple[str, ...] | list[str], now: float) -> dict:
        record = {"ts": now, "name": name, "text": text}
        with self._lock:
            self._last_fired[name] = now
            self._recent.append(record)
            self._recent = self._recent[-8:]
        # → hormones (the whole point)
        try:
            from ..body.endocrine import get_endocrine, Event
            get_endocrine().ingest_event(Event(
                type=f"world_{name}", source="world",
                valence=valence, intensity=intensity, tags=list(tags),
            ))
        except Exception as exc:
            logger.warning("endocrine ingest failed for %s: %s", name, exc)
        # → her day record
        try:
            from ..mind.journal import journal
            journal().log_event("world_event", text, meta={"name": name}, ts=now)
        except Exception:
            pass
        logger.info("world event: %s (%r)", name, text)
        return record

    # ------------------------------------------------------------------

    def recent_fragment(self, window_s: float = RECENT_WINDOW_S) -> str:
        """Most recent event still fresh enough to color the felt-state context."""
        now = time.time()
        with self._lock:
            for rec in reversed(self._recent):
                if now - rec["ts"] <= window_s:
                    return f"just now, {rec['text']}"
        return ""

    def recent(self, n: int = 5) -> list[dict]:
        with self._lock:
            return list(self._recent[-n:])

    # ------------------------------------------------------------------

    @staticmethod
    def _split_key(weather: str) -> str:
        for key in _WEATHER_SHIFTS:
            if key in weather:
                return key
        return weather.split()[0] if weather else ""

    def _safe_weather(self) -> str:
        try:
            from .room.weather import get_weather
            return str(get_weather().current_state()).lower()
        except Exception:
            return ""

    def _safe_awake(self) -> bool:
        try:
            from ..body.sleep import get_sleep, SleepState
            return get_sleep().state not in (SleepState.ASLEEP, SleepState.FALLING_ASLEEP)
        except Exception:
            return True

    def _load(self) -> None:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._last_fired = {str(k): float(v) for k, v in data.get("last_fired", {}).items()}
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("event state load failed: %s", exc)

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"last_fired": self._last_fired}), encoding="utf-8")
        except Exception as exc:
            logger.warning("event state save failed: %s", exc)


# ----------------------------------------------------------------------

_INSTANCE: WorldEventEngine | None = None
_INSTANCE_LOCK = threading.Lock()


def get_world_events() -> WorldEventEngine:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = WorldEventEngine()
    return _INSTANCE


async def run_world_events_loop() -> None:
    await asyncio.sleep(25)
    engine = get_world_events()
    while True:
        try:
            from shared.utils.config import get_settings
            enabled = bool(get_settings().get("world", {}).get("events", {}).get("enabled", True))
            if enabled:
                engine.tick(TICK_SECONDS)
        except Exception as exc:
            logger.warning("world event tick failed: %s", exc)
        await asyncio.sleep(TICK_SECONDS)
