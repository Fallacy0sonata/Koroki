# Morning handoff — Koro, read this first ☀️

Ran the night shift on the live-test checklist. Stack is up and healthy (Brain :9881, IndexTTS :9000,
Orchestrator :9882 all responding). Full detail in `FINDINGS.md`; this is just what YOU need to do.

## ✅ Done overnight (autonomous)
- **Pre-flight** — clean (VRAM idle, doctor.ps1 all green).
- **Boot + smoke** — stack up. Worldstate has all the new sections (activity.current/today, events.recent),
  and felt-context correctly carries her live activity.
- **Mind systems — journal writing confirmed live**: boot thought → mood sample (~30 min) → activity
  transition (bed→window), all landed in `data/koroki/journal/2026-07-03.jsonl` with correct cadence + meta.
- **Endocrine live** — melatonin rising toward sleep across monitor snapshots.
- **Overnight monitor** — `cowork_tests/overnight_worldstate.jsonl` logging worldstate every 10 min.

## 🔧 I fixed one real bug
`scripts/cleanup_port_9000.ps1` was assigning to `$pid` (a read-only PowerShell automatic var), so on a
port conflict it killed *itself* instead of the process holding :9000 — that's why IndexTTS kept failing to
bind. Rewrote it to iterate `OwningProcess`. IndexTTS binds cleanly now. (Details in FINDINGS.md.)

## 🐞 Two bugs I flagged but did NOT touch (your call)
1. **`/ready` shows tts:false even when IndexTTS is healthy.** The readiness check + auth/world voice cues
   ping the dead QwenTTS port :9880; the real chat voice path uses adapter_url :9000 (works). Fixing means
   repointing `services.tts.url` → :9000 AND fixing the path (`auth.py` calls `/v1/synthesize`, adapter
   exposes `/synthesize`). Didn't want to rewrite the voice path mid-test.
2. **Duplicate journal writes** — some thoughts/events written 2–3× with near-identical timestamps (all at the
   00:28 shutdown flush). Looks like a shutdown-flush or loop double-registration. Root cause is in the emit
   path — no symptom filter per your rules.

## 👉 What needs YOU (couldn't do solo)
These are the [EARS] / Discord items — they always needed your ears. Discord's process needs a fresh
access grant (the approval popup timed out while you slept).

1. **Morning overnight read (do this first — the payoff):**
   - Check `data/koroki/journal/2026-07-02.md` and `2026-07-02.voiced.md` exist (consolidation at rollover).
   - **Read `2026-07-02.voiced.md`** — her first diary entry in her own voice.
   - Look for a `dream` event in `2026-07-03.jsonl` (needs ≥20 min sleep).
   - In Discord, ask her: **"what did you dream about?"** and **"how was yesterday?"** → should be grounded
     in the real day entry, not confabulated.
   - Skim `cowork_tests/overnight_worldstate.jsonl` — did she actually fall asleep? did any world events fire?

2. **Sentence-streaming A/B [EARS]** — baseline a multi-sentence voice reply with the flag off, then flip
   `features.sentence_streaming.enabled: true`, restart orchestrator only, and listen for the pause model.
   Verdict → LEGACY.

3. **LoRA voice fidelity [EARS]** — multi-turn Discord chat; if regressed, restore `adapters/koroki_4b_backup_jun20`.

4. **/sing** a short song → confirm it works AND a `sing` event lands in the journal.

5. **Twitch listen-only** — set `streaming.twitch.channel` to any busy live channel in settings.yaml, then
   `.venv\Scripts\python.exe twitch_bot.py`. Watch for `joined #channel as justinfanNNNNN` and
   `[listen-only] she would say: ...` lines. Ctrl+C when satisfied. (Prepped but not run — it competes with
   the overnight GPU work.)

## Files I created (all in cowork_tests/, safe to delete)
preflight/probe/overnight_monitor scripts + their .bat runners, probe_out.txt, overnight_worldstate.jsonl,
FINDINGS.md, this file. The only change OUTSIDE cowork_tests/ is the cleanup_port_9000.ps1 fix.
