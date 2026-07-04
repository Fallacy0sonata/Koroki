# Phase 2D Prediction Log

**Created:** 2026-06-22.
**Status:** Awaiting live validation.

Same methodology as Phase 2B/2C — bugs predicted ahead of live testing, mapped to specific files/functions so future-you doesn't have to grep.

---

## Lighting (`services/orchestrator/world/room/lighting.py`)

### L1. "Light never gets dim at night"
- **Look at:** `_circadian_light_target()` curve. Should drop toward MIN_CIRCADIAN_LIGHT (0.05) between 8pm-6am.
- **Verify:** Call with hour=2 → should be < 0.2. With hour=13 → should be near MAX.

### L2. "User sets light to 0.8 and it stays bright forever"
- **Look at:** `tick()` drift logic. With DRIFT_TAU_SECONDS=14400 (4h), an override of +0.4 from baseline should drift halfway back in ~3h.
- **Symptom in logs:** If level stays exactly at user's setting hours later, drift isn't running.

### L3. "Light fluctuates constantly"
- **Look at:** DRIFT_TAU_SECONDS — too short causes visible jitter. 4h tau is conservative; can extend.

### L4. "Even at noon the room feels dark"
- **Intentional per identity.** `IDENTITY_BLEND_WEIGHT=0.5` blends circadian with dim identity default. Her room is canonically dim. If you want bright, user has to explicitly call `set_level(0.8+)`.

---

## Ambient (`services/orchestrator/world/room/ambient.py`)

### A1. "Temperature never changes regardless of weather"
- **Look at:** `tick()` — should read `get_weather().current_state()` each tick. If weather always returns "clear," no offset applied.
- **Verify:** Force weather to "snow" → temperature should drift down ~3°C over hours.

### A2. "Room reported as 'cool' when temp is 22°C (normal)"
- **Look at:** `contribute_to_felt_state` thresholds. 19/21/24 °C bands.
- **Probable cause:** User's intuition about "cool/warm" differs from these bands. Adjust.

### A3. "Hours away → comes back to exactly identity defaults"
- **Look at:** DRIFT_TAU_SECONDS (6h). If gap > ~3 tau, drift goes to completion. May want to reduce drift rate so room "remembers" longer.

---

## Weather (`services/orchestrator/world/room/weather.py`)

### W1. "Weather flips every few minutes"
- **Look at:** `tick()` — per-dt probability scaling. With TRANSITION_PROB_PER_HOUR=0.15, real transitions should happen on hour-scale, not minute-scale.
- **Symptom:** Watch logs for `Weather: X → Y (after Ns)`. If N is small, math is wrong.

### W2. "Always stuck in snow / always stuck in clear"
- **Look at:** `_TRANSITIONS` matrix. Each state should have non-zero weight to multiple targets.

### W3. "Weather doesn't survive restart"
- **Look at:** `data/world/weather_state.json`. State should reload via `_load()`.

### W4. "Snow in summer is weird"
- **Phase 2D MVP doesn't have seasonal logic.** Documented. Phase 2E could add month-based tilt.

---

## Identity (`services/orchestrator/world/room/identity.py`)

### ID1. "Room feels generic — no aesthetic"
- **Look at:** ROOM_AESTHETIC_FRAGMENTS — these aren't currently surfaced by anything. They're constants meant to inform future content. If the room feels generic in felt-state, we may want lighting.py to surface "purple-tinted dim light" when level is in canonical range.

### ID2. "Room state never returns to her purple/dim aesthetic"
- **Look at:** lighting.py + ambient.py drift logic. Both should pull toward identity defaults. If user override never decays, drift isn't running.

---

## Integration — interoception.py

### I-2D-1. "Felt state never mentions room/weather"
- **Look at:** `get_felt_state()` — should call `get_lighting().tick()` etc. and merge `room_fragments` into the output.
- **Verify in logs:** orchestrator felt-state log should show `ctx=...` with weather/light hints.

### I-2D-2. "Room tick runs but never appears in output"
- **Look at:** room_fragments merge into body_list and context_text. If fragments are computed but dropped during join, fix the join.

### I-2D-3. "Subsystems error out, kills chat"
- **Look at:** try/except around room ticks. Should be non-fatal (return empty fragments).

---

## Cross-system causal chain to verify in live test

```
extended chat session →
  weather drifts (clear → cloudy → rain) →
  ambient.tick reads new weather → temp drifts down 1.5°C →
  felt-state body: "a chill in the air" →
  felt-state context: "rain against the window, dim purple-tinted light, late night"
  → Koroki's response naturally references the rain mood
```

If any link breaks, find the corresponding section above.
