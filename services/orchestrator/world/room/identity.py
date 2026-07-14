"""
Room identity — Koroki's canonical room aesthetic.

Per the design docs, the room is her situated existence:
purple-tinted dim lighting, soft surfaces, late-night-coded feel. The room IS her
identity-place.

This module exposes the CANONICAL room state as defaults — lighting.py and
ambient.py read these as their "home position" baselines. User adjustments
drift away; without intervention things slowly return to the canonical state
(simulating: this is just how her room is).

Why a separate module: identity constants live in one place and have semantic
meaning (not just "default light = 0.4" but "0.4 is the dim purple-lit baseline
she likes"). When future sessions ask "what's her room supposed to feel like,"
this file is the answer.

═══════════════════════════════════════════════════════════════════════════════
🐛 PREDICTED BUGS:

ID1. Room "feels generic" — fragments don't tag any aesthetic.
   Look at: ROOM_AESTHETIC_FRAGMENTS — these should consistently surface in
   felt-state context line. If interoception isn't pulling them, fix the
   integration in interoception.py.

ID2. User changes light + comes back hours later, still at user's setting.
   Look at: DRIFT_TAU_SECONDS in lighting.py. Should slowly pull toward
   IDENTITY_LIGHT_DEFAULT. Default tau is long (~4h) so it's intentional —
   the room reverts overnight, not within a conversation.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

# ── Canonical defaults (her room's "home position") ──────────────────────

# Lighting: 0..1 where 0=dark, 1=bright. 0.4 = dim, purple-lit, late-night feel.
IDENTITY_LIGHT_DEFAULT = 0.4

# Temperature: degrees Celsius. 21°C = comfortably cool, slightly cozy.
IDENTITY_TEMP_DEFAULT = 21.0

# Humidity: 0..1. 0.45 = moderate, not dry/clinical.
IDENTITY_HUMIDITY_DEFAULT = 0.45

# Ambient sound character (subjective fragment) — what her room sounds like
# at rest with no music. Soft hum, slight clock tick, occasional outside noise.
IDENTITY_AMBIENT_DESCRIPTOR = "soft quiet, low ambient hum"

# Aesthetic fragments — these appear in felt-state context when the room is
# in or near its canonical state. Surfaced sparingly to avoid repetition.
ROOM_AESTHETIC_FRAGMENTS = [
    "purple-tinted dim light",
    "soft surfaces, blanket within reach",
    "late-night-coded space",
]


def get_canonical() -> dict[str, float | str]:
    """Snapshot of all canonical defaults — for diagnostics and lighting/ambient
    drift logic to read from."""
    return {
        "light": IDENTITY_LIGHT_DEFAULT,
        "temp": IDENTITY_TEMP_DEFAULT,
        "humidity": IDENTITY_HUMIDITY_DEFAULT,
        "ambient_descriptor": IDENTITY_AMBIENT_DESCRIPTOR,
    }
