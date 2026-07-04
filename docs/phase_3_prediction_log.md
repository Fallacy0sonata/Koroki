# Phase 3 Prediction Log

**Created:** 2026-06-22.
**Status:** Awaiting live validation.

Same methodology as Phase 2 — bugs predicted ahead of live testing, mapped to specific code locations.

---

## Relationships (`services/orchestrator/social/relationship.py`)

### R1. "Trust never goes up despite many positive interactions"
- **Look at:** `TRUST_GAIN_PER_WARM` (0.005). Intentionally tiny. Even 100 warm interactions = only +0.5 trust.
- **Verify:** This is by design — trust is cross-session, accumulates over weeks. If you expect visible movement in a day, that's wrong expectation, not bug.

### R2. "Trust crashes catastrophically on one conflict"
- **Look at:** `TRUST_LOSS_ON_CONFLICT` (0.05). 10x faster than gain but not catastrophic.
- **Expect:** ~20 conflicts to drop from 0.5 to 0.

### R3. "Owner treated as stranger after restart"
- **Look at:** `is_owner` persistence in `data/social/relationships.json`. Set on creation only.
- **Critical:** If `is_owner` flag isn't persisting, the OWNER_TRUST_FLOOR safety net doesn't apply on reload.

### R4. "Score and trust never persist"
- **Look at:** `data/social/relationships.json`. `_save()` called after every update.

### R5. "Trust drops slowly even when user is active"
- **Look at:** `tick_absence_decay()` — should only apply when `days_absent > 7`. If user is active daily, this never fires.

---

## Residue (`services/orchestrator/social/residue.py`)

### RD1. "Yesterday's conflict has no effect today"
- **Look at:** `RESIDUE_DECAY_TAU_HOURS` (6.0). After 24h, residue is ~2% of original.
- **By design:** residue is short-term (hours, not days). For long-term grudges, the relationship-level trust score handles persistence.

### RD2. "Every interaction immediately maxes out residue"
- **Look at:** `RESIDUE_MAX` per hormone (0.10-0.25). Caps prevent runaway accumulation.
- **Note:** Decay is applied BEFORE adding new delta in `write_residue()`. So old residue doesn't compound.

### RD3. "Residue file grows unbounded"
- **Look at:** No purge logic yet. Phase 3 MVP assumes <10 active users (owner + test).
- **Future:** Phase 4+ might add per-user purge after 30 days of total silence.

### RD4. "Residue applies multiple times per session"
- **Look at:** `chat.py` — `apply_residue_to_endocrine` is called in Step 1b PER MESSAGE, not just session start. May need session-start gating.
- **Realistically:** Each call only applies once because the residue gets injected as a body event which doesn't itself write residue back. But the residue decay is computed each time, which is fine.

### RD5. "Owner's residue doesn't feel owner-specific"
- **Look at:** `apply_residue_to_endocrine` — `is_owner` flag adds `owner_present` tag to the injected event. If endocrine isn't reacting differently, check endocrine event handlers.

---

## Scheduler (`services/orchestrator/meta/scheduler.py`)

### S1. "Koroki messages every 5 minutes — spammy"
- **Look at:** `COOLDOWN_SECONDS` (30 min global) + `DRIVE_COOLDOWNS` per drive (1-4h).
- **If still spammy:** Drives are accumulating too fast. Reduce `DRIVE_ACCUMULATION` rates.

### S2. "Koroki never initiates"
- **Look at:** `DRIVE_THRESHOLD` (0.7). With max drive value 1.0 and weights 0.8-1.2, threshold is reachable but requires accumulated state.
- **Debug:** Log `scheduler.drive_state()` periodically to see what's happening.
- **Common cause:** No tick loop calling `maybe_act()` — the scheduler doesn't run on its own, something has to call it. Phase 3 MVP relies on chat.py post-response, which means no calls when nobody is talking.

### S3. "Koroki messages from sleep state"
- **Look at:** `maybe_act` early return on `SleepState.ASLEEP` or `FALLING_ASLEEP`.

### S4. "Drives reset after restart, long absences ignored"
- **Look at:** `_load()` and `_save()`. State should persist.

### S5. "Care fires for non-owner users"
- **Look at:** care drive accumulation — checks `getattr(state, "is_owner", False)`. If presence doesn't track is_owner correctly, the wrong user could trigger care.

### S6. "All three drives fire at once, weird output"
- **Look at:** `_evaluate()` picks the single highest-weighted drive. Only one fires per tick.

### S7. "Scheduler picks unrealistic users (someone offline for weeks)"
- **Look at:** `_evaluate()` for boredom/restlessness — picks most-recent presence. For care, picks owner specifically. If presence dict has stale entries, results may be weird.

---

## Integration with chat.py

### I-3-1. "Residue applied but no body change"
- **Look at:** `chat.py` Step 1b. The residue.apply_residue_to_endocrine returns True if applied. If returning False, residue magnitudes are below 0.02 threshold (decayed away).

### I-3-2. "Relationship trust doesn't update on warm message"
- **Look at:** `chat.py` Step after endocrine ingest — `_rel_mgr.note_warm_interaction` called only if `affectionate` tag or `valence > 0.5`. Check the emotion classification feeding this.

### I-3-3. "Scheduler never gets a chance to fire"
- **Phase 3 MVP doesn't auto-tick the scheduler.** Need to either:
  - (a) Call `scheduler.maybe_act()` from a background task (the Discord bot loop is a candidate)
  - (b) Call it from chat.py POST-response so it ticks during conversations
  - (c) Future: dedicated background autonomous tick task
- **For real proactive behavior:** option (a) is needed. Phase 3 MVP just ships the scheduler module — wiring it to a tick loop is a follow-up.

---

## Cross-system causal chain to verify in live test

```
new session with owner →
  residue.apply_residue: pulls yesterday's lingering oxytocin (0.05) →
  injects warm-residue event with valence=+0.5 →
  body starts session with elevated oxytocin →
  felt-state body: "warmth in chest" before owner even types
  →
  owner says something warm →
  relationship.note_warm_interaction → trust += 0.005
  residue.write_residue → today's oxytocin residue grows
  →
  session ends, residue persists
  →
  later, owner has been absent 90 min:
  scheduler.maybe_act → care drive accumulates → crosses threshold →
  emits InitiateAction(drive="care", user_id=owner, reason="wanting to check on owner")
  →
  bot listens, generates message via LLM with the care reason as context
```

If any link breaks, find the corresponding section above.
