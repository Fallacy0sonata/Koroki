"""
Koroki Virtual Nervous System — state engine.

Runs a persistent multi-variable state that propagates signals through a causal
graph. Variables have inertia: current drifts toward target at a fixed rate per
update cycle. Environment and events modify targets; inertia prevents instant jumps.

State is persisted to data/nervous_system/state.json and updated every 60 seconds.

Variable layers:
  INTEROCEPTIVE  energy, comfort, social_battery, restlessness
  AFFECT         valence, arousal, openness, sharpness
  COGNITIVE      curiosity, focus

External callers:
  on_social_event(kind)     — fired by chat pipeline on conversation events
  on_activity_event(kind)   — fired when an activity completes (chess, singing, etc.)
  get_state()               — returns current state dict for serializer / prompt injection
  run_loop()                — async background task, call once at startup
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .rumination import tick_rumination

logger = logging.getLogger("orchestrator.nervous_system")

_repo_root = Path(__file__).resolve().parents[3]
_STATE_DIR = _repo_root / "data" / "nervous_system"
_STATE_FILE = _STATE_DIR / "state.json"

_UTC7 = timezone(timedelta(hours=7))
_UPDATE_INTERVAL_S = 60


# ── Variable definition ──────────────────────────────────────────────────────

@dataclass
class Var:
    current: float
    target: float
    rate: float      # max movement per update cycle (60s)
    lo: float = 0.0
    hi: float = 1.0

    def step(self) -> None:
        delta = self.target - self.current
        move = min(abs(delta), self.rate) * (1 if delta >= 0 else -1)
        self.current = max(self.lo, min(self.hi, self.current + move))

    def set_target(self, value: float, *, clamp: bool = True) -> None:
        self.target = max(self.lo, min(self.hi, value)) if clamp else value

    def nudge_target(self, delta: float) -> None:
        self.set_target(self.target + delta)


def _default_vars() -> dict[str, Var]:
    return {
        # INTEROCEPTIVE
        "energy":         Var(current=0.70, target=0.70, rate=0.004),
        "comfort":        Var(current=0.72, target=0.72, rate=0.006),
        "social_battery": Var(current=0.80, target=0.80, rate=0.003),
        "restlessness":   Var(current=0.20, target=0.20, rate=0.003),
        # AFFECT
        "valence":        Var(current=0.62, target=0.62, rate=0.005),
        "arousal":        Var(current=0.52, target=0.52, rate=0.005),
        "openness":       Var(current=0.65, target=0.65, rate=0.004),
        "sharpness":      Var(current=0.48, target=0.48, rate=0.005),
        # COGNITIVE
        "curiosity":      Var(current=0.60, target=0.60, rate=0.004),
        "focus":          Var(current=0.62, target=0.62, rate=0.004),
    }


# ── Causal rules ─────────────────────────────────────────────────────────────
# (source, target_var, weight, sign)
# After each step, each rule nudges target_var.target by:
#   weight * (source.current - 0.5) * sign * 0.08   (scaled to ±0.04 max per update)

_CAUSAL_RULES: list[tuple[str, str, float, int]] = [
    ("energy",         "arousal",    0.40,  1),
    ("energy",         "valence",    0.25,  1),
    ("energy",         "sharpness",  0.20,  1),
    ("comfort",        "openness",   0.35,  1),
    ("comfort",        "sharpness",  0.18, -1),  # comfortable → less edgy
    ("comfort",        "valence",    0.22,  1),
    ("social_battery", "openness",   0.45,  1),  # low battery → less open
    ("social_battery", "valence",    0.18,  1),
    ("restlessness",   "arousal",    0.28,  1),
    ("restlessness",   "sharpness",  0.32,  1),
    ("restlessness",   "openness",   0.22, -1),
    ("valence",        "openness",   0.22,  1),
    ("arousal",        "curiosity",  0.28,  1),
    ("curiosity",      "focus",      0.22,  1),
]

_CAUSAL_SCALE = 0.08


def _apply_causal(vs: dict[str, Var]) -> None:
    for src, tgt, weight, sign in _CAUSAL_RULES:
        if src not in vs or tgt not in vs:
            continue
        influence = weight * (vs[src].current - 0.5) * sign * _CAUSAL_SCALE
        vs[tgt].nudge_target(influence)


# ── Circadian target schedule ─────────────────────────────────────────────────

def _circadian_targets(hour: int) -> dict[str, float]:
    """Energy and arousal targets driven by UTC+7 time of day."""
    if 0 <= hour <= 5:     # late night
        return {"energy": 0.25, "arousal": 0.22, "comfort": 0.78}
    elif 6 <= hour <= 9:   # morning
        return {"energy": 0.52, "arousal": 0.44}
    elif 10 <= hour <= 17: # afternoon
        return {"energy": 0.78, "arousal": 0.60}
    elif 18 <= hour <= 21: # evening
        return {"energy": 0.62, "arousal": 0.50}
    else:                  # late evening 22-23
        return {"energy": 0.42, "arousal": 0.35}


# ── Persistence ───────────────────────────────────────────────────────────────

def _load_state() -> dict[str, Var]:
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        vs = _default_vars()
        for name, d in data.items():
            if name in vs and isinstance(d, dict):
                vs[name].current = float(d.get("current", vs[name].current))
                vs[name].target  = float(d.get("target",  vs[name].target))
        return vs
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return _default_vars()


def _save_state(vs: dict[str, Var]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {k: {"current": round(v.current, 4), "target": round(v.target, 4)}
               for k, v in vs.items()}
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


# ── In-memory state ───────────────────────────────────────────────────────────

_vars: dict[str, Var] = _load_state()
_last_update: float = 0.0

# ── Attention spotlight ───────────────────────────────────────────────────────
# Tracks what Koroki is currently focused on (channel_id str, or "dm:<user_id>").
# Intensity decays at _SPOTLIGHT_DECAY per update cycle (60s → full decay in ~17 min).

_SPOTLIGHT_DECAY = 0.06

@dataclass
class _Spotlight:
    target: str | None = None
    intensity: float = 0.0
    set_at: float = 0.0

_spotlight = _Spotlight()


def set_spotlight(target: str, intensity: float = 0.8) -> None:
    """Called when Koroki becomes actively engaged in a channel or DM."""
    _spotlight.target = target
    _spotlight.intensity = max(0.0, min(1.0, intensity))
    _spotlight.set_at = time.time()
    logger.debug("Spotlight set → %s (intensity=%.2f)", target, intensity)


def get_spotlight() -> tuple[str | None, float]:
    """Returns (target, intensity). intensity=0 means no active focus."""
    return _spotlight.target, _spotlight.intensity


def get_state() -> dict[str, float]:
    """Return current values as a flat {name: value} dict."""
    state = {k: round(v.current, 3) for k, v in _vars.items()}
    state["spotlight_intensity"] = round(_spotlight.intensity, 3)
    return state


# ── Update cycle ──────────────────────────────────────────────────────────────

def _update() -> None:
    global _last_update
    now_utc7 = datetime.now(_UTC7)

    # Apply circadian targets
    circ = _circadian_targets(now_utc7.hour)
    for name, tgt in circ.items():
        if name in _vars:
            _vars[name].set_target(tgt)

    # Step all variables toward their targets
    for v in _vars.values():
        v.step()

    # Propagate causal influences (modifies targets, not currents)
    _apply_causal(_vars)

    # Clamp all after causal nudges
    for v in _vars.values():
        v.target = max(v.lo, min(v.hi, v.target))

    # Decay attention spotlight
    if _spotlight.intensity > 0:
        _spotlight.intensity = max(0.0, _spotlight.intensity - _SPOTLIGHT_DECAY)
        if _spotlight.intensity == 0.0:
            _spotlight.target = None

    # random_spark: ~3%/hour = 0.05%/update-cycle. The only true random element.
    # Fires a surprising mood shift with no cause — represents unexplainable consciousness.
    if random.random() < 0.0005:
        _spark_var = random.choice(["valence", "arousal", "restlessness", "curiosity", "openness"])
        _spark_delta = random.choice([-0.12, -0.10, 0.10, 0.12, 0.15])
        _vars[_spark_var].nudge_target(_spark_delta)
        logger.info("random_spark fired: %s %+.2f (unexplained mood shift)", _spark_var, _spark_delta)

    # Tick rumination queue (it checks its own internal interval)
    tick_rumination()

    _last_update = time.time()
    _save_state(_vars)


# ── External event API ────────────────────────────────────────────────────────

def on_social_event(kind: str) -> None:
    """
    Called by the chat pipeline when social events happen.

    kind:
      "conversation"   — someone is talking to her right now
      "long_absence"   — nobody has talked to her in 6+ hours
      "good_exchange"  — conversation went well (high DPO score, positive reaction)
      "dismissive"     — user was curt or dismissive
    """
    v = _vars
    if kind == "conversation":
        v["restlessness"].nudge_target(-0.08)
        v["social_battery"].nudge_target(-0.06)
    elif kind == "long_absence":
        v["restlessness"].nudge_target(+0.12)
        v["social_battery"].nudge_target(+0.08)
    elif kind == "good_exchange":
        v["valence"].nudge_target(+0.06)
        v["openness"].nudge_target(+0.05)
        v["comfort"].nudge_target(+0.04)
    elif kind == "dismissive":
        v["openness"].nudge_target(-0.06)
        v["comfort"].nudge_target(-0.04)
    logger.debug("Social event: %s → targets updated", kind)


def on_activity_event(kind: str) -> None:
    """
    Called when Koroki completes an activity.

    kind:
      "singing_done"   — just finished a song
      "chess_won"      — won a chess game
      "chess_lost"     — lost a chess game
      "chess_move"     — made a move (thinking was engaged)
    """
    v = _vars
    if kind == "singing_done":
        v["valence"].nudge_target(+0.08)
        v["restlessness"].nudge_target(-0.10)
        v["arousal"].nudge_target(-0.05)   # settled after singing
    elif kind == "chess_won":
        v["valence"].nudge_target(+0.06)
        v["sharpness"].nudge_target(+0.05)
    elif kind == "chess_lost":
        v["valence"].nudge_target(-0.03)
        v["focus"].nudge_target(+0.05)     # she thinks harder after losing
    elif kind == "chess_move":
        v["curiosity"].nudge_target(+0.03)
        v["focus"].nudge_target(+0.04)
    logger.debug("Activity event: %s → targets updated", kind)


# ── Background loop ───────────────────────────────────────────────────────────

async def run_loop() -> None:
    """Background task — update nervous system state every 60 seconds."""
    await asyncio.sleep(5)  # brief startup delay
    while True:
        try:
            _update()
        except Exception as exc:
            logger.warning("Nervous system update failed: %s", exc)
        await asyncio.sleep(_UPDATE_INTERVAL_S)
