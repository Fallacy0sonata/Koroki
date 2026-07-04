# Cowork live-test findings — 2026-07-03 (overnight session)

Session run by Claude in cowork while Koro sleeps. Boot at ~04:33.

## Environment / boot
- Pre-flight: no ComfyUI running; VRAM was ~1.6 GB idle before boot; `doctor.ps1` all green (4 venvs, CUDA 12.8, all model files, config/secrets). ✅
- `koroki_discord.bat` launched Brain+IndexTTS+Orchestrator+Discord. Brain+Orch came up ~04:33. ✅

## BUG 1 — cleanup_port_9000.ps1 killed the wrong process (FIXED)
- IndexTTS failed to bind :9000 on boot (`[Errno 10048] address in use`) — a stale python/powershell held the port, so the adapter exited and TTS never came up.
- Root cause in `scripts/cleanup_port_9000.ps1`: it assigned to `$pid`, which is a **read-only PowerShell automatic variable** (the script's own PID). The assignment threw, `$pid` kept the script's own PID, and it then `Stop-Process`-ed *itself* — never killing the real port holder.
- Fix applied: rewrote to use `$ownerPid` and iterate all `OwningProcess` PIDs. IndexTTS then bound :9000 cleanly and `/health` returns ok. ✅
- This was masking the port conflict on every restart — universal fix, not per-incident.

## BUG 2 — /ready reports tts:false even when IndexTTS is healthy (config drift, NOT fixed — needs Koro's call)
- `/ready` and the auth/world-event voice cues ping `services.tts.url` = `http://127.0.0.1:9880` (dead legacy QwenTTS port).
- The real chat voice path correctly uses `services.tts.adapter_url` = `http://127.0.0.1:9000` (IndexTTS, up).
- So chat voice WORKS; only the readiness flag + ambient/auth voice cues are broken.
- Not a one-line repoint: `auth.py` calls `{url}/v1/synthesize`, but the IndexTTS adapter exposes `/synthesize` (no /v1). Repointing url→9000 also needs the path fixed. Left for Koro to decide (didn't want to silently rewrite the voice path mid-test).

## BUG 3 — duplicate journal writes (NOT fixed — flagged)
- `data/koroki/journal/2026-07-03.jsonl` has thought entries written 2–3× with near-identical timestamps
  (e.g. "she's been getting really into games lately" ×3 at ts 1783013282.93–.95).
- Same pattern on some world_event lines. Suggests the journal `log_event` is being called multiple times
  per logical event (loop double-registration or a retry without dedupe).
- Left for Koro — root cause is in the thought/event emit path, not worth a symptom filter.

## Worldstate at boot (04:38–04:53) — mind subsystems present & live ✅
- NEW sections all present and non-null: `activity.current` {name:daydreaming, doing, spot:bed, minutes},
  `activity.today` ("a thought that stuck: ..."), `events.recent` (empty early, expected).
- `felt.context` DOES carry her activity: "...right now she's spacing out, thinking about nothing much". ✅
- Endocrine live and drifting: melatonin climbed 0.15→0.38 over ~15 min (04:38→04:53) as expected pre-sleep.
  cortisol 0.32→0.40. energy 0.99→0.96. nervous openness/curiosity/focus all drifting. ✅
- Her first thought this boot: "i'm sitting with nothing actual" (saved 04:33). In-character, a little melancholy.

## Journal accumulation — CONFIRMED LIVE ✅
Watched the journal grow in real time after boot (host-side reads; the Linux mount is stale-cached
so bash polling is unreliable — used the file tools + curl probes as the source of truth):
- 04:33 `thought`: "i'm sitting with nothing actual" (boot thought landed in journal)
- 05:02 `mood`: "ground feels stable underfoot"  (first mood sample, ~29 min after boot — matches ~30 min cadence)
- 05:03 `activity` transition: daydreaming(bed) → "watching the city from the window" (spot=window) — first
  activity transition of the boot, logged with meta. Activity loop + mood loop both write to journal correctly.
- Endocrine kept drifting across the two monitor snapshots (melatonin 0.30→0.38, cortisol 0.36→0.41) — she's
  winding toward sleep. Overnight monitor (`overnight_worldstate.jsonl`) is logging a snapshot every 10 min. ✅

## What still needs Koro (couldn't run solo overnight)
- **Discord conversation tests** — Discord's real process (`discord.exe`) needs a fresh access grant; the
  approval dialog timed out while you were asleep. So the [EARS]/chat items couldn't run:
  day-recall ("what have you been up to"), semantic recall, interest drift, sentence-streaming A/B,
  LoRA voice fidelity, /sing → journal, proactive outreach. All of these wanted your ears anyway.
- **Twitch listen-only** — deliberately NOT run overnight: it competes with the Brain/GPU that the
  overnight dream+thought generation needs, and picking a live busy channel blind is unreliable.
  Prepped for a one-double-click run in the morning instead (see MORNING_HANDOFF.md).

## Overnight payoff — maturing on its own
Stack left running. Monitor capturing worldstate every 10 min. When she sleeps ≥20 min the
dream→consolidation→diary chain should fire. Morning read steps in MORNING_HANDOFF.md.
