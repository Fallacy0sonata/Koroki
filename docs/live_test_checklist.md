# Live Test Checklist — one session, everything validated

Written 2026-07-03 for the cowork session. Covers every feature built in the
2026-07-02/03 run that needs live validation, plus pending regressions.

> **STATUS UPDATE (2026-07-03 afternoon, post-overnight-run):**
> ✅ DONE by the overnight cowork session: §0 pre-flight, §1 boot+smoke+worldstate,
> §2 journal-accumulation/felt-context/activity (all confirmed live), §3 partially
> (4 real world events fired: siren/elevator/bird/neighbors — engine works).
> ✅ FIXED since: cleanup_port_9000.ps1 self-kill (overnight session), `/ready` tts:false +
> auth/world voice cues now use the IndexTTS adapter (verify at next boot: /ready shows
> tts:true), "duplicate journal writes" root-caused as TEST POLLUTION → journal cleaned
> (118 artifact entries removed, 43 real kept), tests/contract/conftest.py now isolates
> ALL tests from her real state, and boot-time journal consolidation added (a day that
> ends while the stack is down now consolidates 90 s after next boot).
> ✅ ALSO FIXED (from the side-session IndexTTS boot log): the short-input runaway —
> warmup text lengthened, per-call mel budget proportional to input, streaming min
> sentence length raised to 12. **Verify at next boot:** IndexTTS warmup completes in
> ~2-3 s with NO `max_mel_tokens` RuntimeWarning, and `/ready` shows `tts:true`.
> ⚠ SHE NEVER SLEPT overnight (booted 04:33 with full energy) — §8 overnight payoff
> (dream → consolidation → voiced diary) is STILL PENDING: leave the stack running
> through tonight.
> **REMAINING = §2 Discord asks, §4 streaming A/B, §5 regressions, §6 Twitch, §7 glance,
> §8 tonight, §9 wrap.**

> **LIVE SESSION RESULTS (2026-07-03 afternoon, Fable 5 driving via tester bot):**
> ✅ `/ready` fully green for the first time (tts fix validated) · ✅ day-grounded answers
> ("listening to stuff again" = her real journal) · ✅ semantic recall INSTRUMENTED (zero-keyword
> "feathered musician" → cockatiel memory, score 0.845) · ✅ interest drift INSTRUMENTED (music
> +1.5 delta from one exchange) · ✅ defer-TTS restored (was hardcoded False → 123 s replies;
> now env-driven → 15 s) · ✅ boot-order rule proven twice (TTS must warm before Brain serves).
> ❌ **VOICE IS DOWN under concurrency**: IndexTTS synthesis wedges with the Brain resident
> (11.8/12 GB, spill) — graphs trimmed to [1,4] (INDEX_TTS_GRAPH_BS) but insufficient.
> **AUX-ENCODER CPU OFFLOAD = next session's first task.** Voice items (deferred audio attach,
> /sing, sentence-streaming A/B) all wait on it. Stack left RUNNING for the overnight
> (sleep → dream → diary post to #diary 1522496321792118825 + morning welcome-back greeting).

Legend: ☐ check · **[EARS]** needs owner listening · **[OVERNIGHT]** matures while stack runs

---

## 0 · Pre-flight (VRAM + health)

- ☐ **Antivirus**: the AV flagged/suspended IndexTTS during the unattended overnight run
  (unsigned python + GPU + nobody at the keyboard = classic false positive; a suspended
  process also squats its port). Whitelist the project once (admin PowerShell):
  `Add-MpPreference -ExclusionPath "C:\Users\Shinn\Desktop\Koroki"` — or the equivalent
  in your third-party AV's UI. If a service ever dies silently: check Protection History.
  Note: the AV's HTTPS interception also breaks python SSL — IndexTTS now boots with
  `HF_HUB_OFFLINE=1` (all models cached) to sidestep it.
  2026-07-04: the same shield ("Norton Web/Mail Shield" cert) MITMs `discord.media:8443`
  and kills Discord VOICE (ws handshake timeout — she joins the VC then drops). Fix:
  Web Shield → disable HTTPS scanning, or exception for `discord.media` / venv python.exe.

- ☐ **Kill ComfyUI** — it holds ~8 GB of FLUX weights (PID via
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? {$_.CommandLine -like '*ComfyUI*'}`).
  Brain (4-bit) + IndexTTS need nearly the whole card.
- ☐ `nvidia-smi` → memory.used should drop under ~1.5 GB before boot.
- ☐ `.\scripts\doctor.ps1` → all green (4 venvs, CUDA, model files, ports free).

## 1 · Boot + smoke

- ☐ `.\scripts\koroki_discord.bat` (Brain + IndexTTS + Orchestrator + Discord bot).
- ☐ Watch orchestrator log for the new startup lines:
  - `embedding backfill: N nodes` … `embedding backfill done` (first boot after the
    embeddings feature will embed the whole memory stream on CPU — expect ~30 ms/node).
  - activity loop + world-events loop start without errors.
- ☐ `.\scripts\smoke_test.ps1` passes.
- ☐ `GET :9882/v1/worldstate` — verify NEW sections exist and are non-null:
  `activity.current` (name/doing/spot/minutes), `activity.today`, `events.recent`.

## 2 · Mind systems (live behavior)

- ☐ **Journal accumulating**: `data/koroki/journal/<today>.jsonl` exists and grows —
  expect `activity` events on transitions, `mood` samples every ~30 min, `thought`
  entries every 45–110 min.
- ☐ **Felt-state carries her activity**: orchestrator log line `Felt state: ... ctx=`
  should include `right now she's <doing something>`.
- ☐ **Ask her about her day** (Discord): "what have you been up to today?" →
  answer should reference actual logged activities, not confabulation. **[EARS]**
- ☐ **Semantic recall**: reference an old conversation topic *without shared keywords*
  (e.g. if you discussed a song, ask "that music thing we talked about?") → she should
  connect it. Check `data/mind/memory_embeddings.jsonl` is growing with new memories.
- ☐ **Interest drift**: have a genuinely engaging exchange about one of her topics
  (music is highest-weight) → check `data/mind/interest_drift.json` gains a delta and
  log shows `interest reinforced: ...`.

## 3 · World events

- ☐ Within the session, `worldstate.events.recent` should eventually list something
  (elevator ding / neighbor noise / siren are the most frequent; rates are per-hour, so
  give it 1–3 h of runtime). Each event should also appear in the journal jsonl as
  `world_event`, and — within 5 min of firing — in the felt-state ctx line
  (`just now, ...`).
- ☐ Endocrine reacted: orchestrator log shows the ingest and the felt state shifts
  (e.g. a siren nudges alertness fragments).

## 4 · Sentence-streaming TTS **[EARS — the big one]**

- ☐ Baseline: with flag OFF, time a multi-sentence Discord voice reply (note perceived wait).
- ☐ Flip `config/settings.yaml` → `features.sentence_streaming.enabled: true`,
  restart orchestrator only.
- ☐ Multi-sentence replies: log should show `Sentence-streaming TTS active` then
  `Sentence-stream TTS OK: n/n sentences`. Compare perceived latency vs baseline
  (expect 30–50 % faster on 3+ sentence replies).
- ☐ **Listen for the pause model**: trail-offs ("…") should breathe noticeably;
  run-on sentences ("And then—") should flow with no artificial gap; paragraph-ish
  topic shifts get the longest beat. Nothing should sound randomly choppy.
- ☐ Voice tradeoff check: streaming mode skips cross-sentence cue injections —
  does she still sound like herself? If flat, note it (emo_vector should carry emotion).
- ☐ Fallback sanity: at least once you'll likely see
  `Sentence-stream fell back to one-call TTS (text_changed_after_stream)` — that's
  correct behavior, not a bug. Audio should still arrive (slower path).
- ☐ **Verdict**: keep flag on / revert. (Revert = set false, restart orchestrator.)

## 5 · Pending regressions from before this run

- ☐ **LoRA voice-test** (pending since 2026-06-28 retrain): character fidelity +
  multi-turn discipline in Discord. If regressed → restore `adapters/koroki_4b_backup_jun20`. **[EARS]**
- ☐ **Singing**: `/sing` a short song → works end-to-end AND the journal now gets a
  `sing` event (check jsonl; it renders as "## Singing" in tomorrow's day entry).
- ☐ **Proactive**: leave her idle 2–6 h during the session → organic outreach message
  arrives, in character, or `[silent]` logged. (This also feeds `interaction` journal events.)

## 6 · Twitch surface (no credentials needed)

- ☐ Set `streaming.twitch.channel: <any busy live channel>` in settings.yaml.
- ☐ `.venv\Scripts\python.exe twitch_bot.py` → log shows `joined #channel as justinfanNNNNN`.
- ☐ Watch selection behavior: name-mentions of "koroki" (if any) always considered;
  ambient picks should be OCCASIONAL in busy chat (not spammy). Log lines
  `[listen-only] she would say: ...` — read a few: sane, in character? **[EARS-ish]**
- ☐ Ctrl+C when satisfied. (Replying needs TWITCH_TOKEN + TWITCH_NICK in .env +
  `respond: true` — a later, owned-channel test.)

## 7 · Frontend glance (optional — frontend is paused/other models)

- ☐ Open `:9882/scene.html` full-screen: living bedroom holds up? Note composition
  nits for the frontend session (don't fix now).

## 8 · **[OVERNIGHT]** — leave the stack running

She needs to live through a night once for the full loop:

- ☐ Evening: energy/melatonin should put her to sleep (worldstate `presence.sleep_state`
  → asleep; activity → `sleeping`).
- ☐ Morning checks, in order:
  - `data/koroki/journal/<yesterday>.md` exists (consolidation ran at sleep/rollover).
  - `<yesterday>.voiced.md` exists — **read it: her first diary entry in her own voice.**
  - A `dream` event in today's jsonl (needs ≥ 20 min sleep; renders as "## Dreamt").
  - **Ask her: "what did you dream about?"** → she tells you the actual dream. **[EARS]**
  - Ask her: "how was yesterday?" → grounded in the real day entry.

## 9 · Wrap-up

- ☐ Decide flags: `sentence_streaming` on/off, `twitch` config kept?
- ☐ Note any failures with log snippets → they become queue items.
- ☐ Update `docs/master_queue.md` QUEUE STATE + LEGACY with verdicts
  (especially: sentence-streaming ears verdict, LoRA voice verdict, first-journal quality).
- ☐ Restart ComfyUI afterward only if art work resumes.
