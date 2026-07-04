# External Research Scan (2026-06-28)

User-supplied links, triaged against the CLAUDE.md test: *does it serve the captain-in-cabin,
sentient-subsystem, self-hosted, zero-budget goal — or fight it?* Verdicts so a future session
doesn't re-evaluate.

| Source | What it is | Verdict for Koroki |
|---|---|---|
| **Autodata** (Meta RAM) | Agentic auto-generation/curation of training data (gen → solve → verify → refine loop). Open-ish. | 🟢 **KEEP — most useful here.** Directly serves roadmap Phase 5/6 (retrain on lived experience) AND our LoRA/singing data generation. The verify-loop pattern is exactly how to manufacture quality Koroki training data. Revisit when we build the data pipeline. |
| **PerceptionDLM** (PKU) | Open multimodal diffusion-LM for efficient visual region understanding/captioning. Weights on HF. | 🟢 **KEEP — for "she can see."** Candidate for a future *vision subsystem* (visual acknowledgement). Open + efficient + parallel. Captain-in-cabin compatible: it's a subsystem that feeds felt/percept state to the captain, not a replacement for her. |
| **Wan-Streamer v0.1** (Alibaba) | End-to-end single-Transformer real-time audio-visual interactive model (sees/hears/talks on video, ~200ms, 25fps). Research POC. | 🟡 **INSPIRATION ONLY — architecturally OPPOSED.** It collapses perception+reasoning+TTS+avatar into ONE monolithic model — the exact antithesis of captain-in-cabin ("the LLM is not Koroki; the subsystems are"). Adopting it would dissolve her whole architecture, and it's not 12GB-feasible. BUT steal its UX north star: its idle state never freezes — "identity, gaze, posture, breathing, subtle facial motion over streaming history." That's the aliveness bar for our Live2D idle. [paper](https://arxiv.org/abs/2606.25041) |
| **Lift4D** | 4D (3D+time) reconstruction of dynamic objects from monocular video (deformable Gaussian splatting). Open. | 🟡 Future-only. We chose 2.5D/Live2D, not 3D. Bookmark if we ever build a true 3D Koroki from video. |
| **Arbor** | Controllable 3D asset generation via constraint meshes (frozen TRELLIS). Open-ish. | 🔴 Low. Offline 3D, not avatar/real-time. Only if we go 3D props someday. |
| **DanceOPD** | Despite the name, image gen/editing via on-policy flow-matching distillation. Open. | 🔴 Skip. Not animation. SDXL covers our image needs; this is research-level distillation. |
| **Un-0** | Image gen via coupled Kuramoto oscillators (novel, not diffusion). Open, but trained on B200s; FID 6.74 @64×64 (early-diffusion quality). | 🔴 Skip. Fascinating, not production; not 12GB-practical; quality far below SDXL-anime. |
| **Seed 2.1** (ByteDance) | Multimodal agent model (Pro/Turbo). **API-only, no open weights.** | 🔴 Skip. Cloud API + closed = fails budget + self-host rules. Competitor data point only. |
| **Sakana Fugu** | Multi-frontier-LLM orchestration API (TRINITY + Conductor). **Cloud API.** | 🔴 Skip. Cloud + frontier-model routing = against budget/self-host. She already has her own orchestrator. |
| **OpenAI×Broadcom "Jalapeño" inference chip** | Hyperscaler custom inference silicon. | 🔴 N/A. Industry hardware; we self-host on a consumer GPU. |
| **IBM sub-1nm chip** | Semiconductor research, years from consumer. | 🔴 N/A now. |
| **Aleph ultrasound brain** | Neurotech/BCI. | 🔴 N/A to Koroki. |
| **Ornith 1.0** (deep-reinforce) | — | ⚪ **Unread** — site returned 403. Re-check if it matters. |
| **Happy Horse 1.1** | — | ⚪ **Unread** — not fetched this pass. |

## Takeaways
1. **Autodata** is the genuinely actionable find — pattern for our future training-data pipeline.
2. **PerceptionDLM** is the leading candidate the day we give her sight (a perception subsystem).
3. **Wan-Streamer** is the cautionary mirror: the industry trend is monolithic end-to-end; Koroki's bet
   is the opposite (composable subsystems). Take its *aliveness* bar, reject its architecture.
4. Everything cloud-API or closed-weight (Seed 2.1, Fugu) is auto-excluded by the budget/self-host rules.
