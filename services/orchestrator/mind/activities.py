"""Activity engine — what Koroki is DOING right now, off-stream.

Before this module the world simulated weather/time/lighting and drives fired reach-outs,
but there was no notion of her current occupation. This subsystem decides it (captain-in-
cabin: the decision is embodied state, not an LLM call), so that:
  - prompts can say "right now you're curled up reading" (felt-state context),
  - proactive messages have substance ("I was reading and…") instead of generic check-ins,
  - the journal records a real day instead of a void,
  - the Living Avatar's teleport spots get their "which spot is she at" signal for free.

Selection = weighted sampling over a home-activity catalog, scored by hour-of-day fit ×
energy fit × weather × mild endocrine nudges (restless ↑ pacing/window, warm ↑ cozy).
Sleep states override everything. Dwell times have organic jitter; the previous activity
is halved so she doesn't loop. State persists across restarts. No LLM on any path.
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

from ..world import clock
from .journal import journal, KIND_ACTIVITY

logger = logging.getLogger("orchestrator.mind.activities")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_PATH = _REPO_ROOT / "data" / "mind" / "activity_state.json"

TICK_SECONDS = 60.0


@dataclass(frozen=True)
class ActivityDef:
    name: str
    doing: str                       # present-tense phrase for prompts/journal
    spot: str                        # avatar spot hint (bed/window/desk/center/kitchen…)
    hours: tuple[int, int]           # inclusive local-hour band (wraps midnight if a > b)
    energy: tuple[float, float]      # comfortable energy range 0..1
    weight: float = 1.0
    dwell_min: float = 20 * 60       # seconds
    dwell_max: float = 75 * 60
    rain_boost: float = 1.0          # multiplier when it rains
    restless_boost: float = 1.0      # multiplier when cortisol/NE high
    cozy_boost: float = 1.0          # multiplier when oxytocin high


CATALOG: list[ActivityDef] = [
    ActivityDef("reading", "curled up with a book", "bed", (8, 26), (0.25, 0.9),
                weight=1.3, rain_boost=1.5, cozy_boost=1.3),
    ActivityDef("listening_to_music", "lying back listening to music", "bed", (9, 27), (0.15, 0.8),
                weight=1.1, cozy_boost=1.2),
    ActivityDef("watching_the_city", "watching the city from the window", "window", (6, 30), (0.1, 0.9),
                weight=1.0, rain_boost=1.6, restless_boost=1.6),
    ActivityDef("singing_practice", "quietly practicing a song", "center", (10, 22), (0.5, 1.0),
                weight=0.9, dwell_min=15 * 60, dwell_max=40 * 60),
    ActivityDef("doodling", "doodling in a sketchbook", "desk", (10, 24), (0.35, 0.9),
                weight=0.9),
    ActivityDef("browsing", "aimlessly scrolling around the net", "desk", (8, 27), (0.2, 0.85),
                weight=1.0, dwell_min=10 * 60, dwell_max=45 * 60),
    ActivityDef("playing_chess", "playing chess against the engine", "desk", (12, 25), (0.4, 0.95),
                weight=0.7, dwell_min=15 * 60, dwell_max=50 * 60),
    ActivityDef("tea_break", "making tea and snacking on something", "kitchen", (7, 25), (0.2, 0.9),
                weight=0.8, dwell_min=8 * 60, dwell_max=20 * 60, cozy_boost=1.3),
    ActivityDef("tidying", "half-heartedly tidying the room", "center", (9, 21), (0.55, 1.0),
                weight=0.5, dwell_min=8 * 60, dwell_max=25 * 60, restless_boost=1.5),
    ActivityDef("stretching", "stretching out on the rug", "center", (7, 23), (0.3, 0.9),
                weight=0.5, dwell_min=6 * 60, dwell_max=15 * 60, restless_boost=1.3),
    ActivityDef("daydreaming", "spacing out, thinking about nothing much", "bed", (6, 30), (0.0, 0.7),
                weight=0.8, cozy_boost=1.1),
    ActivityDef("napping", "dozing off for a bit", "bed", (13, 17), (0.0, 0.35),
                weight=0.7, dwell_min=25 * 60, dwell_max=70 * 60),
]

SLEEPING = ActivityDef("sleeping", "asleep", "bed", (0, 24), (0.0, 1.0))


def _hour_fits(h: float, band: tuple[int, int]) -> bool:
    a, b = band  # b may exceed 24 to wrap past midnight (e.g. (8, 26) = 08:00–02:00)
    return a <= h <= b or a <= h + 24 <= b


@dataclass
class ActivityState:
    name: str = "daydreaming"
    doing: str = "spacing out, thinking about nothing much"
    spot: str = "bed"
    since_ts: float = field(default_factory=time.time)
    until_ts: float = field(default_factory=lambda: time.time() + 1800)


class ActivityEngine:
    def __init__(self, state_path: Path | None = None):
        self._lock = threading.Lock()
        self._state_path = state_path or _STATE_PATH
        self._state = ActivityState()
        self._load()

    # ------------------------------------------------------------------

    def current(self) -> dict:
        with self._lock:
            s = self._state
            return {
                "name": s.name, "doing": s.doing, "spot": s.spot,
                "since_ts": s.since_ts, "minutes": round((time.time() - s.since_ts) / 60, 1),
            }

    def prompt_fragment(self) -> str:
        """Short phrase for the felt-state context line."""
        with self._lock:
            if self._state.name == "sleeping":
                return ""
            return f"right now she's {self._state.doing}"

    # ------------------------------------------------------------------

    def tick(self) -> None:
        """Called every ~60s. Handles sleep override and dwell-expiry transitions."""
        try:
            asleep = self._sleep_override()
        except Exception:
            # Never let this be silent again — a broken sleep-override kept her
            # awake every night with zero log evidence (2026-07-04).
            logger.warning("sleep override failed — assuming awake", exc_info=True)
            asleep = False
        now = time.time()
        with self._lock:
            cur = self._state
            if asleep:
                if cur.name != "sleeping":
                    self._transition(SLEEPING, now, forced=True)
                return
            if cur.name == "sleeping":
                self._transition(self._pick(now), now)  # she woke up
                return
            if now >= cur.until_ts:
                self._transition(self._pick(now), now)

    def _sleep_override(self) -> bool:
        # 2026-07-04 ROOT CAUSE of "she daydreamed in bed all night": this used
        # `get_sleep().state`, which DOESN'T EXIST (the attr is private `_state`)
        # → AttributeError every tick → the silent except in tick() defaulted to
        # awake → the sleeping activity never once fired. current_state() is the
        # public accessor (same one /v1/worldstate uses).
        from ..body.sleep import get_sleep, SleepState
        state = get_sleep().current_state()
        return state in (SleepState.ASLEEP, SleepState.FALLING_ASLEEP)

    def _pick(self, now: float) -> ActivityDef:
        h = clock.hour_of_day()
        energy = self._safe_energy()
        weather = self._safe_weather()
        hormones = self._safe_hormones()
        restless = max(hormones.get("cortisol", 0.3) - 0.3,
                       hormones.get("norepinephrine", 0.3) - 0.3, 0.0) * 2  # 0..~1
        cozy = max(hormones.get("oxytocin", 0.3) - 0.3, 0.0) * 2

        candidates: list[tuple[ActivityDef, float]] = []
        for a in CATALOG:
            if not _hour_fits(h, a.hours):
                continue
            w = a.weight
            lo, hi = a.energy
            if energy < lo:
                w *= max(0.1, 1 - (lo - energy) * 3)
            elif energy > hi:
                w *= max(0.2, 1 - (energy - hi) * 2)
            if "rain" in weather or "storm" in weather or "drizzle" in weather:
                w *= a.rain_boost
            w *= 1 + (a.restless_boost - 1) * restless
            w *= 1 + (a.cozy_boost - 1) * cozy
            if a.name == self._state.name:
                w *= 0.4  # don't loop the same thing
            if w > 0:
                candidates.append((a, w))
        if not candidates:
            return SLEEPING if energy < 0.15 else CATALOG[-2]  # daydreaming fallback
        defs, weights = zip(*candidates)
        return random.choices(defs, weights=weights, k=1)[0]

    # activities that carry a multi-day project (mind/projects.py)
    _PROJECT_KINDS = {"reading": "book", "singing_practice": "song", "doodling": "art"}

    def _transition(self, a: ActivityDef, now: float, forced: bool = False) -> None:
        dwell = random.uniform(a.dwell_min, a.dwell_max)
        doing = a.doing
        meta: dict = {"name": a.name, "spot": a.spot, "forced": forced}
        # attach the ongoing project so her life has arcs, not just moments
        kind = self._PROJECT_KINDS.get(a.name)
        if kind:
            try:
                from .projects import get_projects
                proj = get_projects().touch(kind)
                if proj is not None and not proj.done:
                    doing = f"{a.doing} — \"{proj.name}\""
                    meta["project"] = proj.name
            except Exception:
                logger.debug("project touch skipped", exc_info=True)
        self._state = ActivityState(name=a.name, doing=doing, spot=a.spot,
                                    since_ts=now, until_ts=now + dwell)
        self._save()
        journal().log_event(KIND_ACTIVITY, doing, meta=meta, ts=now)
        logger.info("activity -> %s (%s, ~%.0f min)", a.name, a.spot, dwell / 60)

    # ------------------------------------------------------------------

    def _safe_energy(self) -> float:
        try:
            from ..body.energy import get_energy
            return float(get_energy().level())
        except Exception:
            return 0.6

    def _safe_weather(self) -> str:
        try:
            from ..world.room.weather import get_weather
            return str(get_weather().current_state()).lower()
        except Exception:
            return ""

    def _safe_hormones(self) -> dict[str, float]:
        try:
            from ..body.endocrine import get_endocrine
            snap = get_endocrine().snapshot()
            levels = snap.get("levels", snap)
            return {k: float(v) for k, v in levels.items() if isinstance(v, (int, float))}
        except Exception:
            return {}

    def _load(self) -> None:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._state = ActivityState(**data)
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("activity state load failed: %s", exc)

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(self._state.__dict__), encoding="utf-8")
        except Exception as exc:
            logger.warning("activity state save failed: %s", exc)


# ----------------------------------------------------------------------
# Singleton + loop
# ----------------------------------------------------------------------

_INSTANCE: ActivityEngine | None = None
_INSTANCE_LOCK = threading.Lock()


def get_activities() -> ActivityEngine:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = ActivityEngine()
    return _INSTANCE


async def run_activity_loop() -> None:
    """Background task: tick the engine + sample a mood journal event every ~30 min."""
    await asyncio.sleep(20)  # let subsystems come up
    engine = get_activities()
    mood_counter = 0
    while True:
        try:
            engine.tick()
            mood_counter += 1
            if mood_counter >= 30:  # ~every 30 ticks = 30 min
                mood_counter = 0
                _sample_mood()
        except Exception as exc:
            logger.warning("activity loop tick failed: %s", exc)
        await asyncio.sleep(TICK_SECONDS)


def _sample_mood() -> None:
    try:
        from ..body.interoception import get_felt_state
        felt = get_felt_state()
        mood = felt.mood or felt.body or "even, nothing notable"
        journal().log_event("mood", mood)
    except Exception as exc:
        logger.debug("mood sample failed: %s", exc)
