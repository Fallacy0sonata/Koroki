# Phase 2C Prediction Log

**Purpose:** Same methodology as Phase 2B. Bugs predicted ahead of live testing, mapped to specific files/functions.

**Created:** 2026-06-21.
**Status:** Awaiting live validation.

---

## Energy subsystem (`services/orchestrator/body/energy.py`)

### E1. "She never gets sleepy even after a long day"
- **Look at:** `BASE_DRAIN_RATE` constant.
- **Probable cause:** Drain over short test windows (minutes) is invisible — energy decays over hours. Verify by checking the raw level in felt-state telemetry after a 30+ min session.
- **Tuning:** Currently 3.3e-5/sec — depletes 1.0 → 0.15 over 16h. Adjust if testing over 6-8h shows energy too high.

### E2. "Energy stuck at 0 — never recovers even after sleep"
- **Look at:** `tick_asleep()` — verify it's being called from `SleepSystem.tick()` when state is ASLEEP/FALLING_ASLEEP.
- **Verify in logs:** Should see `Sleep state: wake → falling_asleep` then `→ asleep`, then energy level rising.

### E3. "Active interaction doesn't drain energy faster than idle"
- **Look at:** `note_interaction()` calls in chat.py + `ACTIVE_DRAIN_MULTIPLIER`.
- **Test:** Send 10 rapid messages; energy should drop noticeably faster than during equivalent idle time.

### E4. "Energy reads as full after a brain restart even if it was empty"
- **Look at:** `save()` calls — currently NO automatic save is wired in chat.py. Energy state will reset on restart.
- **Decision:** Whether to add `save()` to chat.py periodic / shutdown hooks is a Phase 3 question.

---

## Sleep state machine (`services/orchestrator/body/sleep.py`)

### SL1. "She gets stuck in FALLING_ASLEEP forever"
- **Look at:** `FALLING_ASLEEP_DURATION_SECONDS` + the transition condition in `tick()`.
- **Mechanism:** After `FALLING_ASLEEP_DURATION_SECONDS` (5 min) in FALLING_ASLEEP, should auto-advance to ASLEEP regardless of energy.

### SL2. "She never wakes up even after 8 hours of sleep"
- **Look at:** ASLEEP → WAKING transition: `cur_energy >= WAKE_FROM_REFILL_ENERGY` OR `mel < WAKE_MELATONIN_CEILING`.
- **Probable cause:** If neither condition triggers (e.g. nighttime test where melatonin is high), she'd stay asleep. The morning melatonin fall is the natural wake signal.
- **Fix candidate:** Add a max-sleep-duration fallback (~10h forced wake).

### SL3. "She wakes up the moment user sends a message"
- **Look at:** `external_arousal_event()`. Currently only wakes from light sleep (< 1h in ASLEEP). Deeper sleep is sticky.
- **Verify:** If she's been asleep 30 min, a message should wake her (via WAKING transition). If she's been asleep 2h, the message should NOT wake her — she sleeps through it. Real Discord/web should respect this.

### SL4. "Sleep debt never accumulates / always reads 0"
- **Look at:** `sleep_debt_hours` update logic in `tick()`. Accumulates only when `state == WAKE` AND `wake_seconds > TARGET_WAKE_SECONDS`.
- **Probable cause:** `last_full_sleep_ts` getting reset accidentally. Verify it's only set in the ASLEEP → WAKING transition.

### SL5. "Cortisol baseline isn't affected by sleep deprivation"
- **Look at:** `EndocrineEngine.tick()` — should call `get_sleep().cortisol_baseline_multiplier()` and multiply circadian target.
- **Cross-reference:** If sleep system never accumulates debt (SL4), multiplier stays at 1.0 and cortisol baseline reads as normal.

### SL7. **(CAUGHT IN SMOKE TEST, FIX APPLIED)** "She wakes up the instant she falls asleep during the day"
- **Look at:** `MIN_SLEEP_FOR_MELATONIN_WAKE_SECONDS` constant + the wake-from-morning gate in `ASLEEP → WAKING` transition.
- **Root cause:** The morning-wake condition `mel < WAKE_MELATONIN_CEILING (0.2)` is ALWAYS true during daytime (mel=0 between roughly 7am and 8pm per circadian curve). Without a minimum-sleep-duration gate, any daytime nap wakes immediately on the next tick — she enters ASLEEP, then the wake check fires on melatonin alone, transitioning straight to WAKING.
- **Mechanism:** Real biology: melatonin rises at night, falls in morning. Daytime mel=0 should mean nothing if she's already napping — it's only a "wake signal" if she went to sleep at night and morning arrived.
- **Fix shipped:** Melatonin-wake requires `in_state_for >= MIN_SLEEP_FOR_MELATONIN_WAKE_SECONDS` (4h). Below that duration, only energy refill can wake her. Naps now work.
- **Verification:** Discovered in Phase 2C smoke test TEST B output showing `sleep=waking` immediately after ASLEEP entry at midday. Fix verified — she can nap.

### SL6. "Brain restart loses sleep state"
- **Look at:** `data/body/sleep_state.json`. Save needs explicit call — not automatic per-tick yet.
- **Phase 2C decision:** No auto-save. Restart resets to WAKE. Document if changed.

---

## Memory consolidation (`services/orchestrator/meta/sleep_cycle.py`)

### SC1. "No consolidation visible — recent memories unchanged after sleep"
- **Look at:** `_on_sleep()` callback wiring. Verify `get_sleep_cycle()` was called at least once during runtime so the callbacks fired into `SleepSystem`.
- **Test:** Look in logs for `Sleep cycle: consolidation marked pending` and then `Sleep cycle consolidation: X recent memories scanned`.

### SC2. "Consolidation fires repeatedly during ASLEEP"
- **Look at:** `_consolidation_pending` flag — reset after `consolidate()` runs.
- **Should be:** One consolidation per sleep cycle.

### SC3. "Phase 2C says semantic layer not implemented yet"
- **Known limitation.** Phase 3 will add `services/orchestrator/mind/semantic/` for that layer.

---

## Cross-system integration (chat.py)

### I-2C-1. "She never gets tired during chat sessions"
- **Look at:** `get_energy().note_interaction()` call in chat.py body integration block.
- **Mechanism:** Each chat request bumps `last_activity_ts`. Next tick within 60s applies `ACTIVE_DRAIN_MULTIPLIER`.

### I-2C-2. "Cortisol reads as elevated even after a fresh wake"
- **Look at:** Sleep debt clearing in ASLEEP → WAKING transition.
- **Probable cause:** Debt was high; sleeping cleared it. If still reads high, check `sleep_debt_hours = 0.0` line is being reached.

### I-2C-3. "Energy ticks happening even when she's asleep (wrong direction)"
- **Look at:** `SleepSystem.tick()` — branches on state and calls `tick_awake` OR `tick_asleep`. Verify the conditional is correct.

---

## Cross-system causal chain to verify in live test

```
extended chat session over 8+ hours →
  energy drains, cortisol baseline rises mildly (sleep debt) →
  felt-state begins reporting "soft tiredness" then "low energy, blinking slower" →
  eventually energy < 0.25 OR melatonin high enough →
  sleep state machine triggers FALLING_ASLEEP →
  next 5 min: "eyes wanting to close, slipping under" →
  ASLEEP →
  memory consolidation fires (high-importance memories retained, low decayed) →
  energy refills, cortisol baseline normalizes →
  energy ≥ 0.85 OR morning melatonin drop →
  WAKING state for 3 min ("just-awake fog, not all the way back yet") →
  WAKE: she's herself again, refreshed, sleep_debt_hours = 0
```

If ANY link breaks, find the corresponding section above.
