"""Phase 2D smoke test — room subsystems + felt-state integration."""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Clean state
for p in [
    REPO_ROOT / "data" / "world" / "lighting_state.json",
    REPO_ROOT / "data" / "world" / "ambient_state.json",
    REPO_ROOT / "data" / "world" / "weather_state.json",
]:
    if p.exists():
        p.unlink()

from services.orchestrator.world.room.lighting import get_lighting, _circadian_light_target, _identity_blended_target
from services.orchestrator.world.room.ambient import get_ambient
from services.orchestrator.world.room.weather import get_weather, VALID_STATES
from services.orchestrator.world.room.identity import (
    IDENTITY_LIGHT_DEFAULT, IDENTITY_TEMP_DEFAULT, IDENTITY_HUMIDITY_DEFAULT,
)
from services.orchestrator.body.interoception import get_felt_state


def main() -> None:
    print("=" * 70)
    print("PHASE 2D SMOKE TEST — room subsystems")
    print("=" * 70)

    # ── TEST A: circadian light curve sanity ──
    print("\n[A] Circadian light curve at different hours:")
    for h in [2, 6, 9, 13, 17, 21]:
        c = _circadian_light_target(h)
        b = _identity_blended_target(h)
        print(f"  hour={h:2}: circadian={c:.2f}  identity-blended={b:.2f}")

    # ── TEST B: lighting drift toward identity ──
    print("\n[B] Lighting drift — user override + simulated hours pass:")
    light = get_lighting()
    print(f"  initial level: {light.level():.2f} (should be ~{IDENTITY_LIGHT_DEFAULT})")
    now = time.time()
    light.set_level(0.85, now_ts=now)
    print(f"  after set_level(0.85): {light.level():.2f}")
    for hours in [1, 4, 12]:
        light.tick(now_ts=now + hours * 3600)
        print(f"  after {hours:2}h: {light.level():.2f}")

    # ── TEST C: ambient temperature follows weather ──
    print("\n[C] Ambient temperature with different weather:")
    weather = get_weather()
    ambient = get_ambient()
    now = time.time()
    for state in ["clear", "rain", "snow"]:
        weather.set_state(state, now_ts=now)
        # Reset ambient to identity to see drift fresh
        ambient._state.temperature_c = IDENTITY_TEMP_DEFAULT
        ambient._state.last_tick_ts = now
        for hours in [6, 24]:
            ambient.tick(now_ts=now + hours * 3600)
        print(f"  weather={state:6}: temp after 24h drift = {ambient.temperature_c():.1f}°C  "
              f"humidity = {ambient.humidity():.2f}")

    # ── TEST D: weather state machine ──
    print("\n[D] Weather state machine — simulating 1 week:")
    weather.set_state("clear", now_ts=now)
    transitions = 0
    last = "clear"
    for hours in range(1, 24 * 7 + 1):
        weather.tick(now_ts=now + hours * 3600)
        cur = weather.current_state()
        if cur != last:
            transitions += 1
            last = cur
    print(f"  transitions in 1 week: {transitions} (expect ~25, with TRANSITION_PROB_PER_HOUR=0.15)")

    # ── TEST E: full felt-state with room subsystems ──
    print("\n[E] Full felt-state snapshot with room contribution:")
    weather.set_state("rain", now_ts=time.time())
    # Force ambient toward cool
    ambient._state.temperature_c = 18.5
    light.set_level(0.25)
    fs = get_felt_state()
    print(f"  body:    {fs.body}")
    print(f"  mind:    {fs.mind}")
    print(f"  mood:    {fs.mood}")
    print(f"  context: {fs.context}")
    print("  (expect: cool / rain / dim purple in the output somewhere)")

    print("\n" + "=" * 70)
    print("PHASE 2D SMOKE TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
