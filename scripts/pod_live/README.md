# Pod Live Rehearsal — 3090 organ-residency rehearsal

Runs Koroki's GPU organs on a rented **secure-cloud RTX 3090** while the local PC
keeps only what must stay home (game, Discord bot, orchestrator, ears-CPU). This
is the "day in the 3090 life" rehearsal: gaming + watching + hearing + the
production 4B persona simultaneously — physically impossible on the 12GB card.

```
LOCAL (4070 Ti free for the game)          POD (RTX 3090, secure cloud)
  discord_bot.py + ears (CPU whisper)        Brain :9881  4B EXL2 + configured production LoRA
  orchestrator :9882                         Voice :9000  IndexTTS2 (the 3090 production plan)
  the game + capture                         Vision :9005 moondream2 fp16 (no VRAM diet needed)
        |                                          ^
        +---- ssh -L 9881/9000/9005 ---------------+   (localhost both ends, no public ports)
```

## Important: this is not the routed full-potential brain yet

The current recipe launches one production 4B brain on `:9881`. Minecraft's
`GOAL_BRAIN_URL` therefore defaults to the same endpoint, so director/QC calls
are still the 4B checking itself. The 8B-reasoner + 4B-persona work in
`experiments/brain_split/` is a validated prototype, not part of `pod_run.sh`.

Do not describe this rehearsal as the full-potential version until all of these
are true:

1. IndexTTS uses the measured dieted build (~5 GB rather than the stock ~9.7 GB).
2. The pod runs persona `:9881` (4B + production LoRA) and reasoner `:9883`
   (base 8B, no mismatched 4B LoRA) concurrently without paging.
3. Local tunnels include `:9883` and set `GOAL_BRAIN_URL=http://127.0.0.1:9883`
   for Minecraft while commentary continues through the 4B/orchestrator.
4. The routed personal-question veto, curated transitions, and a Minecraft
   factual acceptance set pass live—not only the structural prototype probes.
5. The resident brain + voice + vision stack stays below a safe VRAM ceiling
   under simultaneous load, with latency and recovery recorded.

The 3090 makes that version practical. It does not replace Minecraft's
deterministic navigation, retry memory, inventory economy, or verification.

## Flow
1. `sync_to_pod.ps1 -PodIp <ip> -PodPort <port>` — uploads code + private pack
   (the LoRA currently selected in settings + voice samples; nothing else
   private, no .env).
2. On pod: `bash /workspace/koroki/scripts/pod_live/pod_setup.sh` (installs +
   public model downloads, ~10-20 min) then `bash .../pod_run.sh`.
3. Local: `start_rehearsal.ps1 -PodIp <ip> -PodPort <port>` — opens tunnels,
   patches settings (tts adapter_url -> :9000, backup kept), starts orchestrator
   + bot. NO supervisor (it would fight the tunnels), NO local GPU services.
4. Play, watch, talk, VC. Everything routes through the pod transparently.
5. Done: close the rehearsal window (settings auto-restore), **terminate the pod**
   (private files die with it), relaunch normal stack via launch_koroki.ps1.

## Notes
- Voice contract is engine-agnostic: orchestrator just points at :9000 and gets
  IndexTTS2 with emo_vector emotion — the actual production plan for the card.
- Latency adds one RTT per service call (Thailand<->pod region). Pick the
  closest data center offered.
- Battery traps all handled in pod_setup.sh (PEP 668, caches on volume,
  py3.11 for IndexTTS, paged/flash-attn quirks live in repo code already).
