# Phase 2B Prediction Log

**Purpose:** Built before live-testing. When you eventually do live-test, this maps observable symptoms to specific code locations so you don't have to grep the whole repo.

**Created:** 2026-06-21 (Phase 2B build day).
**Status:** Awaiting live validation.

---

## How to use this doc

When you live-test and see a weird behavior:
1. Find the matching symptom below.
2. The "Look at" pointer tells you which file + which function.
3. Check the "Probable cause" first — most common.
4. If that's not it, check the "Less likely but possible" list.

If you see a symptom NOT in this doc, that's a new failure mode worth investigating from scratch.

---

## Presence subsystem (`services/orchestrator/social/presence.py`)

### S1. "She doesn't get warmer over a long owner session"
- **Look at:** `OWNER_INTENSITY_MULTIPLIER` (top of file) + `is_owner` check in `note_activity()` and `maybe_emit_sustained_events()`.
- **Probable cause:** `chat.py` not passing `is_owner=True` correctly to `note_activity`. Verify line in chat.py that calls `get_presence().note_activity(_user_id, is_owner=_is_owner)`.
- **Less likely:** `OWNER_INTENSITY_MULTIPLIER` set too low (default 1.5 — increase to 2.0+ if owner sessions still feel cold).
- **Telemetry:** orchestrator log should show `Presence: sustained_presence emitted user=<id> threshold=Xs intensity=Y` lines.

### S2. "She gets warm fast over rapid messaging but not over long sessions"
- **Look at:** `SUSTAINED_THRESHOLDS` constant — these are wall-clock-seconds-into-session, not message-count gates.
- **Probable cause:** Session resets are triggering too aggressively (gap > `ACTIVE_WINDOW_SECONDS`). If user pauses 6 minutes mid-conversation, session resets and thresholds re-fire from zero.
- **Fix candidates:**
  - Raise `ACTIVE_WINDOW_SECONDS` to 10 min.
  - OR add "soft session" — pause forgives gap up to 15 min if total session time > threshold.

### S3. "Oxytocin blasts to 1.0 and stays after first sustained_presence event"
- **Look at:** `last_sustained_event_ts` update logic and `fired_thresholds_seconds` list.
- **Probable cause:** Threshold gating is broken — same threshold fires every tick.
- **Symptom in logs:** Repeated `Presence: sustained_presence emitted user=X threshold=60` for same threshold value within seconds.
- **Fix:** Verify `presence.fired_thresholds_seconds.append(threshold_seconds)` runs AFTER each emit and before next tick check.

### S4. "After idle return, immediately gets max sustained_presence event"
- **Look at:** Session reset logic in `note_activity()` — lines that set `existing.session_start_ts = ts` etc. when gap > active window.
- **Probable cause:** `fired_thresholds_seconds = []` reset not happening on session restart.
- **Fix:** Verify the session-reset block clears all three: `session_start_ts`, `last_sustained_event_ts`, `fired_thresholds_seconds`.

### S5. "Multi-user chats cause wrong user's presence to drive oxytocin"
- **Look at:** `chat.py` step 3 — the sustained_presence emit loop forwards `sp_event.is_owner` to determine if owner_present tag goes through.
- **Probable cause:** A non-owner's sustained_presence event sneaking owner_present tag.
- **Fix:** Verify the `if sp_event.is_owner` check before appending owner_present tag.

### S6. "Presence state lost on brain restart"
- **Look at:** `_load()` in `PresenceTracker`. Default state path: `data/social/presence_state.json`.
- **Probable cause:** State never saved. Add a `presence.save()` call somewhere — periodic save, or on shutdown.
- **Decision needed:** Do we WANT cold-start presence behavior on restart? Real bodies have a different state when you wake up. For now, no automatic save is wired in — sessions reset on every restart, which is arguably correct.

---

## Memory subsystem (`services/orchestrator/mind/memory.py`)

### M1. "Memory store fills up rapidly — every message becomes a memory"
- **Look at:** `MIN_IMPORTANCE_TO_WRITE` constant (default 0.25) + `_compute_importance_from_body()`.
- **Probable cause:** Body state hasn't been activated yet (early in conversation), so importance scores look like noise.
- **Verify:** Check orchestrator logs for `Memory written: id=X importance=Y content=Z` — what are the importance values clustering around?
- **Fix candidates:**
  - Raise threshold to 0.35.
  - Require multi-hormone activation (currently any single deviation passes).

### M2. "Recall returns the same memory over and over"
- **Look at:** `retrieve()` — verify `last_accessed_ts` is set on returned nodes.
- **Probable cause:** Recency score after access not actually pushing the memory down. We DO update last_accessed_ts but the score formula doesn't include it — recency is age-since-WRITE, not age-since-ACCESS.
- **Fix:** Either:
  - (a) Add an access-penalty to score (subtract small constant for each recent access).
  - (b) Change recency formula to use `max(age_since_write, age_since_access)`.
- **Phase 2B keeps it simple** — known limitation, addressed in 2C.

### M3. "Memory recall feedback creates oscillation"
- **Look at:** `apply_recall_feedback()` — the cooldown check.
- **Probable cause:** `last_feedback_ts` not updating, OR `RECALL_COOLDOWN_SECONDS` (60s default) too short for the rate at which we retrieve.
- **Check:** If chat.py is calling retrieve() AND apply_recall_feedback() for the same memory within 60s, we should see one feedback and then nothing. If logs show repeated feedback for same memory ID, cooldown is broken.
- **Fix:** Verify the early-return in apply_recall_feedback when within cooldown.

### M4. "Importance scores all cluster around 0.5"
- **Look at:** `_compute_importance_from_body()` — uses absolute deviation from baseline.
- **Probable cause:** Body baselines need to be calibrated against rolling window, not static defaults. Static defaults work fine while body is at rest; once body is in "elevated state" (e.g. session-long elevated cortisol), small deltas don't trigger high importance.
- **Phase 2B keeps it static** — known limitation.

### M5. "Old memories never retrieved even when relevant"
- **Look at:** `RECENCY_DECAY_HOURS` constant (default 36 hours).
- **Probable cause:** With 36h half-life, a memory from a week ago has recency ~0.02. Even with high importance and high relevance, may not surface.
- **Fix candidates:** Raise to 72h, OR raise BETA_IMPORTANCE relative to ALPHA_RECENCY in score weights.

### M6. "Relevance ranking is bad"
- **Look at:** `_relevance_text_overlap()` — Jaccard-style.
- **Probable cause:** This IS known weak. The captain probably says "Yorushika" while a memory says "music." No overlap, no relevance score.
- **Phase 2B known limitation** — Phase 2C replaces with real embeddings.
- **Workaround until then:** Memory importance + recency carry most of the signal; relevance is best-effort.

### M7. "Memory file gets corrupted (one bad line breaks everything)"
- **Look at:** `_load()` — has try/except per line to skip corrupt lines.
- **Probable cause:** If `_append_to_disk` was interrupted mid-write, partial line remains.
- **Verify:** orchestrator log on startup should say `Memory loaded: X nodes, Y corrupt lines skipped`.

### M9. **(CAUGHT IN SMOKE TEST, FIX APPLIED)** "Recall feedback produces disappointment instead of warm echo"
- **Look at:** `Event.skip_rpe` field + `EndocrineEngine.ingest_event()` RPE bypass branch.
- **Root cause:** Without `skip_rpe=True` on recall echo events, RPE engine sees a state transition from previous high-V external state to a new low-V echo state → big negative δ → phasic dopamine crashes negative → felt-state inverts from warm to disappointed.
- **Mechanism:** TD-learning math: δ = reward + γ·V(s_new) - V(s_prev). If V(s_prev) is high (from previous warm external events) and V(s_new) is 0 (echo state never seen), δ is heavily negative regardless of echo event's positive valence.
- **Fix shipped:** `apply_recall_feedback()` sets `skip_rpe=True` on all echo events. `ingest_event()` short-circuits RPE entirely when flag is set.
- **Verification:** This was discovered in the Phase 2B smoke test (TEST D). The test scenario with prior warm V buildup → "warm memory recall" → expected oxytocin nudge → actually got "small disappointment" because RPE δ went strongly negative. Fix verified: with skip_rpe=True, recall behaves correctly.

### M8. "After restart, no recall feedback ever fires"
- **Look at:** `apply_recall_feedback()` — checks node.body_state_at_write.
- **Probable cause:** Old memories written before endocrine integration have empty `body_state_at_write`, so feedback can't fire.
- **Acceptable:** New memories written from now on will work. Historical memories are silent.

---

## Endocrine ↔ Memory ↔ Presence integration (`services/orchestrator/routes/chat.py`)

### I1. "Felt state has no sustained_presence effect even after 10 min owner session"
- **Look at:** chat.py "Step 3: emit any sustained_presence events" block.
- **Probable cause:** The owner check is wrong, or sustained_presence event isn't being ingested into endocrine.
- **Verify in orchestrator log:** Should see both `Presence: sustained_presence emitted ...` AND a subsequent `Felt state: body=... oxytocin=...` showing rise.
- **Less likely:** Endocrine engine tick not running between sp_event ingest and felt_state read.

### I2. "Memory recall doesn't seem to affect body"
- **Look at:** chat.py "Step 4: retrieve relevant memories" block.
- **Probable cause:** `top_k=3` retrievals happening but `apply_recall_feedback` returns False for all (cooldown OR no notable body state at write).
- **Telemetry:** Add a debug log in `apply_recall_feedback` to track when feedback fires vs is blocked.

### I3. "Memory write logged but doesn't persist"
- **Look at:** `MemoryStream._append_to_disk()` — disk write error.
- **Probable cause:** `data/mind/` directory permission issue, OR encoding issue with message content (Thai/CJK chars). The code uses `ensure_ascii=False` so JSON should handle it, but file permission can still fail silently.
- **Verify:** Check whether `data/mind/memory_stream.jsonl` exists and is growing.

### I4. "Step 4 (memory recall) takes a long time as memory grows"
- **Look at:** `retrieve()` — O(n) scan over all memories per request.
- **Phase 2B accepted:** n is small (~hundreds of memories). Phase 3 may add index or tiered storage.
- **Warning sign:** Brain request timing should show stable ms even after weeks of memories. If retrieval > 100ms, time to optimize.

---

## Cross-system causal chain to verify in live test

The whole point of Phase 2B is that this chain works:

```
user spends sustained time online with Koroki
  → presence tracker says "owner session is 5+ min old"
  → emits sustained_presence event
  → endocrine ingests event → oxytocin baseline rises
  → felt-state says "warmth in your chest"
  → next response: captain reads warmer felt-state, responds warmer
  → memory of THIS warm moment gets written (importance is high)
  → tomorrow when something comes up reminiscent → memory recalled
  → recall feedback small oxytocin nudge
  → today's response carries echo of yesterday's warmth
```

**If ANY link in this chain breaks, find the corresponding section above.**
