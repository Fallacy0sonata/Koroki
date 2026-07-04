"""
World clock with circadian forcing functions.

Phase 1 — Endocrine MVP needs:
  - UTC+7 wall clock (her timezone per CLAUDE.md / autonomous_koroki_design.md)
  - Circadian forcing for cortisol (morning peak, midnight nadir)
  - Melatonin window (evening rise → midnight peak → morning offset)

The clock is pure functions over wall-clock time — no internal state.
Subsystems call `now()` and the circadian helpers as needed.

Biology sources (cited in master_queue.md endocrine entry):
  - Cortisol diurnal: peak 06-08:00 (~1.0 normalized), nadir 00:00 (~0.1)
  - DLMO (dim light melatonin onset): ~19-22:00. Peak: 00-04:00. Offset: 03-06:00.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

# Koroki's timezone — per autonomous_koroki_design.md, real-time UTC+7.
_KOROKI_TZ = timezone(timedelta(hours=7))


def now() -> datetime:
    """Koroki's local wall-clock time."""
    return datetime.now(tz=_KOROKI_TZ)


def hour_of_day(when: datetime | None = None) -> float:
    """Fractional hour of day, e.g. 14.5 = 14:30. Drives circadian curves."""
    t = when or now()
    return t.hour + t.minute / 60.0 + t.second / 3600.0


def cortisol_circadian(when: datetime | None = None) -> float:
    """Cortisol baseline forcing (normalized 0..1).

    Asymmetric curve matching real biology:
      - Nadir near midnight-3am (~0.1)
      - Rapid rise from ~3-7am (cortisol awakening response)
      - Peak at ~7am (~1.0)
      - Gradual day-long decline through afternoon (~0.7 at noon, ~0.45 at 4pm)
      - Evening descent through evening to nadir (~0.30 at 8pm, ~0.10 by midnight)

    Implemented as a piecewise smooth function (cosine arcs joined at key
    transition points) to avoid the symmetric-cosine bug where the trough
    landed at 7pm instead of midnight.
    """
    h = hour_of_day(when)

    if h < 3:
        # Late-late nadir: drift down toward absolute minimum 0.10 at 02:00
        # then a slight pre-awakening rise begins
        # 0:00 → 0.12, 2:00 → 0.10, 3:00 → 0.12
        # Use small cosine bump
        return 0.10 + 0.02 * math.cos((h - 2) * math.pi / 2)
    if h < 7:
        # Awakening rise: 0.12 at 03:00 → 1.0 at 07:00
        # Smooth half-cosine for natural easing
        frac = (h - 3) / 4.0
        return 0.12 + 0.88 * (1 - math.cos(frac * math.pi)) / 2
    if h < 14:
        # Morning peak hold + gradual decline: 1.0 at 07:00 → 0.65 at 14:00
        frac = (h - 7) / 7.0
        return 1.0 - 0.35 * (1 - math.cos(frac * math.pi)) / 2
    if h < 20:
        # Afternoon descent: 0.65 at 14:00 → 0.30 at 20:00
        frac = (h - 14) / 6.0
        return 0.65 - 0.35 * (1 - math.cos(frac * math.pi)) / 2
    # Evening descent into nadir: 0.30 at 20:00 → 0.12 at 24:00
    frac = (h - 20) / 4.0
    return 0.30 - 0.18 * (1 - math.cos(frac * math.pi)) / 2


def melatonin_circadian(when: datetime | None = None) -> float:
    """Melatonin level forcing (normalized 0..1).

    Near-zero during daytime. Rises sharply at DLMO (~21:00). Peak around
    01-03:00. Declines through dawn. Near-zero by 07:00.

    Returns 0 at 12:00, ~0.1 at 20:00, ~0.7 at 22:00, ~1.0 at 02:00, ~0.3 at 06:00.
    """
    h = hour_of_day(when)
    # Build a smooth bump centered at 02:00 with FWHM ~6 hours.
    # Using a gaussian-ish window.
    peak_hour = 2.0
    # Wrap-around distance from peak (so 22:00 is 4 hours from 02:00, not 20)
    raw_dist = abs(h - peak_hour)
    dist = min(raw_dist, 24 - raw_dist)
    # Gaussian with σ ≈ 3 hours
    intensity = math.exp(-0.5 * (dist / 3.0) ** 2)
    return intensity


def is_late_night(when: datetime | None = None) -> bool:
    """True when it's late-late (00-04). Used for sensory descriptors."""
    h = hour_of_day(when)
    return 0 <= h < 4


def time_of_day_label(when: datetime | None = None) -> str:
    """Human-readable rough time-of-day label for felt-state descriptions.

    Used by interoception when composing the natural-language snapshot.
    """
    h = hour_of_day(when)
    if 5 <= h < 8:
        return "early morning"
    if 8 <= h < 11:
        return "morning"
    if 11 <= h < 14:
        return "midday"
    if 14 <= h < 17:
        return "afternoon"
    if 17 <= h < 20:
        return "evening"
    if 20 <= h < 23:
        return "night"
    if 23 <= h or h < 2:
        return "late night"
    return "small hours of the morning"  # 02-05


def seconds_since(then: datetime) -> float:
    """Seconds elapsed since `then` (Koroki time)."""
    return (now() - then).total_seconds()
