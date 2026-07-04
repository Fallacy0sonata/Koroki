"""
Global Koroki mood modifier system.

Modifiers are Koroki's own state conditions (sick, tired, well_rested, etc.) that
scale how strongly emotional triggers move her affect. They're Koroki-global — shared
across all user interactions, not per-user.

Two modifier sources:
  Persistent — stored in data/mood/modifiers.json, decay toward 1.0 over time
  Transient  — time-of-day scale computed from UTC+7 clock, never stored

compute_global_modifier_scale() returns the combined multiplier to pass into nudge_emotion().
add_modifier() is the public API for injecting new conditions (sick, well_rested, etc.).

Scale of 1.0 = neutral. <1.0 dampens emotional response. >1.0 amplifies it.
Combined scale is clamped to [0.15, 2.5] so no modifier combination fully locks
Koroki out of or into extreme emotional response.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("orchestrator.mood_modifiers")

_repo_root = Path(__file__).resolve().parents[2]
_MOOD_DIR = _repo_root / "data" / "mood"
_MODIFIER_FILE = _MOOD_DIR / "modifiers.json"

_UTC7 = timezone(timedelta(hours=7))

_SCALE_MIN = 0.15
_SCALE_MAX = 2.5


@dataclass
class Modifier:
    type: str
    magnitude: float       # initial multiplier (0.0–2.0, 1.0 = neutral)
    created_at: float      # unix timestamp
    expires_at: float      # unix timestamp
    decay_per_hour: float = 0.0  # how fast magnitude returns toward 1.0 per hour

    def effective_magnitude(self) -> float:
        """Interpolates from initial magnitude toward 1.0 as time passes."""
        if self.decay_per_hour <= 0:
            return self.magnitude
        hours_elapsed = (time.time() - self.created_at) / 3600.0
        t = min(1.0, hours_elapsed * self.decay_per_hour)
        return self.magnitude + (1.0 - self.magnitude) * t


def load_modifiers() -> list[Modifier]:
    try:
        with open(_MODIFIER_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [
                Modifier(
                    type=str(d["type"]),
                    magnitude=float(d["magnitude"]),
                    created_at=float(d["created_at"]),
                    expires_at=float(d["expires_at"]),
                    decay_per_hour=float(d.get("decay_per_hour", 0.0)),
                )
                for d in data
                if isinstance(d, dict)
            ]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return []


def save_modifiers(mods: list[Modifier]) -> None:
    _MOOD_DIR.mkdir(parents=True, exist_ok=True)
    with open(_MODIFIER_FILE, "w", encoding="utf-8") as f:
        json.dump([asdict(m) for m in mods], f, ensure_ascii=False, indent=2)


def _prune_expired(mods: list[Modifier]) -> list[Modifier]:
    now = time.time()
    return [m for m in mods if m.expires_at > now]


def add_modifier(
    type: str,
    magnitude: float,
    duration_hours: float,
    decay_per_hour: float = 0.0,
) -> None:
    """Add or replace a named modifier. magnitude < 1.0 dampens, > 1.0 amplifies."""
    now = time.time()
    mods = _prune_expired(load_modifiers())
    mods = [m for m in mods if m.type != type]
    mods.append(
        Modifier(
            type=type,
            magnitude=magnitude,
            created_at=now,
            expires_at=now + duration_hours * 3600,
            decay_per_hour=decay_per_hour,
        )
    )
    save_modifiers(mods)
    logger.info(
        "Modifier added: type=%s magnitude=%.2f duration=%.1fh decay=%.3f/hr",
        type, magnitude, duration_hours, decay_per_hour,
    )


def remove_modifier(type: str) -> None:
    mods = _prune_expired(load_modifiers())
    mods = [m for m in mods if m.type != type]
    save_modifiers(mods)


def _time_of_day_scale(utc7_hour: int) -> float:
    """Emotional reactivity scale based on UTC+7 hour.

    Models natural human alertness/reactivity rhythm.
    Late night (0-5):  very dampened — tired, slow to process
    Morning (6-9):     groggy, subdued
    Day (10-17):       baseline
    Evening (18-21):   relaxed, slightly more open
    Late evening (22-23): winding down
    """
    if 0 <= utc7_hour <= 5:
        return 0.55
    elif 6 <= utc7_hour <= 9:
        return 0.80
    elif 10 <= utc7_hour <= 17:
        return 1.0
    elif 18 <= utc7_hour <= 21:
        return 1.08
    else:  # 22-23
        return 0.85


def compute_global_modifier_scale(utc7_now: datetime | None = None) -> float:
    """Combined scale: persistent modifiers × time-of-day curve. Clamped to [0.15, 2.5].

    Pass this as modifier_scale into nudge_emotion(). A scale of 1.0 means neutral —
    same trigger delta as baseline. <1.0 dampens, >1.0 amplifies.
    """
    if utc7_now is None:
        utc7_now = datetime.now(_UTC7)

    mods = _prune_expired(load_modifiers())

    persistent_scale = 1.0
    for m in mods:
        persistent_scale *= m.effective_magnitude()

    tod_scale = _time_of_day_scale(utc7_now.hour)
    combined = persistent_scale * tod_scale
    return max(_SCALE_MIN, min(_SCALE_MAX, combined))


def get_active_modifier_labels() -> list[str]:
    """Return type strings of all currently active persistent modifiers (for core_facts injection)."""
    return [m.type for m in _prune_expired(load_modifiers())]


_SOCIAL_LOG_FILE = _MOOD_DIR / "social_log.json"
_SOCIAL_LOG_MAX_AGE_S = 86400  # 24 hours


def _load_social_log() -> list[dict]:
    try:
        with open(_SOCIAL_LOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


def _save_social_log(log: list[dict]) -> None:
    _MOOD_DIR.mkdir(parents=True, exist_ok=True)
    with open(_SOCIAL_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False)


def track_social_interaction(user_id: str) -> None:
    """Record a conversation event and auto-adjust social load modifiers.

    Thresholds:
      ≥8 conversations in the last hour  → social_fatigue (0.72×, 3h, decays in ~4h)
      ≥4 conversations in the last hour  → social_saturation (0.85×, 1.5h, decays in ~2h)
      ≥6 hours since last conversation   → social_loneliness (1.15×, expires after first interaction)
    Fatigue and saturation are mutually exclusive (fatigue takes priority).
    Loneliness is removed as soon as any conversation happens.
    """
    now = time.time()
    log = _load_social_log()

    # Remove the loneliness modifier — someone is talking to her now
    remove_modifier("social_loneliness")

    # Append new entry and prune entries older than 24h
    log.append({"ts": now, "user_id": str(user_id)})
    cutoff = now - _SOCIAL_LOG_MAX_AGE_S
    log = [e for e in log if e.get("ts", 0) >= cutoff]
    _save_social_log(log)

    # Compute load metrics
    hour_ago = now - 3600
    conversations_last_hour = sum(1 for e in log if e.get("ts", 0) >= hour_ago)

    # Apply social load modifier based on conversation frequency
    if conversations_last_hour >= 8:
        add_modifier("social_fatigue", magnitude=0.72, duration_hours=3.0, decay_per_hour=0.25)
    elif conversations_last_hour >= 4:
        # Only set saturation if fatigue isn't already active
        existing = [m.type for m in _prune_expired(load_modifiers())]
        if "social_fatigue" not in existing:
            add_modifier("social_saturation", magnitude=0.85, duration_hours=1.5, decay_per_hour=0.5)
    else:
        # Below threshold — clear fatigue/saturation if it's decayed enough to not matter
        pass  # let natural decay handle it


def check_and_apply_loneliness() -> None:
    """Call periodically (e.g. on autonomy tick) to apply loneliness modifier if idle too long."""
    log = _load_social_log()
    if not log:
        hours_idle = 99.0
    else:
        last_ts = max(e.get("ts", 0) for e in log)
        hours_idle = (time.time() - last_ts) / 3600.0

    if hours_idle >= 6.0:
        # More receptive after long quiet — slight amplifier, expires after next interaction
        add_modifier("social_loneliness", magnitude=1.15, duration_hours=12.0, decay_per_hour=0.0)
        logger.info("Social loneliness modifier applied (idle %.1fh)", hours_idle)


def apply_startup_modifier() -> None:
    """Inject a startup_groggy modifier if Koroki was offline for ≥2 hours (simulates waking up)."""
    try:
        mtime = _MODIFIER_FILE.stat().st_mtime
        offline_hours = (time.time() - mtime) / 3600.0
    except FileNotFoundError:
        offline_hours = 99.0

    if offline_hours >= 2.0:
        # Groggy for 30 min, fully decays over that window (decay_per_hour=2.0 → 100% at 0.5h)
        add_modifier("startup_groggy", magnitude=0.70, duration_hours=0.5, decay_per_hour=2.0)
        logger.info("Startup groggy modifier applied (offline %.1fh)", offline_hours)
    else:
        logger.debug("Startup: offline only %.1fh — no groggy modifier", offline_hours)
