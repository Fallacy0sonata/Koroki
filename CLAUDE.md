# Koroki Ascension 2.0 — Claude Instructions

## Generational Legacy Log
`LEGACY.md` in the project root is the long-term project history file. It records model versions, architectural decisions, bug root causes, and lessons learned — with enough context to understand WHY, not just what.

**Write to `LEGACY.md` when:**
- A new DiffSinger model version is started or completed
- A bug is found and root-caused (add to "Bug History" section)
- A training approach is tried and produces a clear result (good or bad)
- An architectural decision is made that future sessions should understand
- A lesson is learned the hard way

**Do not write ephemeral session details** (current task status, in-progress debugging). Only write things that will still be meaningful in 3 months.

## Quality Philosophy
**Time is not a constraint on this project. Always do the complete, correct solution.**
- No cheap fixes, patches, or workarounds that defer the real problem
- No synthetic shortcuts if hand-crafted quality is achievable
- If the root cause requires a rewrite or retraining, do it — don't band-aid the symptom
- When something is broken at the data/architecture level, fix it there, not downstream
- "Good enough for now" is not acceptable if the right solution is known

## What This Is
AI character named Koroki. Discord-first, web-second. Three-tier personality system driven by relationship score. Voice synthesis with emotion vectors.

## Architecture (Ports & Roles)
```
Orchestrator  :9882   services/orchestrator/       entry point, routing, auth, emotion engine, streaming
Brain         :9881   services/brain/              LLM (Qwen3-8B, 4-bit NF4), adapter manager — LoRA bypassed
TTS           :9880   services/tts/                voice synthesis (QwenTTS legacy, replaced by IndexTTS)
IndexTTS      :9000   experiments/index-tts/       primary TTS engine (Python 3.11 venv: .venv_indextts)
Singing v1    :9001   experiments/singing/         RVC pipeline — yt-dlp → demucs → Applio RVC → mix
                                                   (Python 3.11 venv: .venv_singing)
Singing v2    :9002   experiments/singing-v2/      Seed-VC pipeline — yt-dlp → demucs → Seed-VC → mix
                                                   Better singing quality, zero-shot from speech samples
                                                   (Python 3.11 venv: .venv_singing_v2)
Singing v3    :9003   experiments/diffsinger/      DiffSinger full synthesis — active default
                                                   Adapter runs in main .venv; sing_song.py uses .venv_diffsinger
                                                   Start: .\scripts\easy_start_singing_diffsinger_adapter.ps1
Discord Bot          discord_bot.py               primary UI, /sing slash command → :9882/v1/sing
Web Client           clients/web/                 secondary UI, Live2D canvas
```

## Personality Adapters (LoRA)
| Adapter    | Trigger                    | Behavior                   |
|------------|----------------------------|----------------------------|
| owner      | is_owner=True              | warm, affectionate, ASMR   |
| tsundere   | relationship_score >= 50   | playful, teasing           |
| peasant    | relationship_score < 50    | cold, formal, distant      |

Selection: `services/brain/adapters.py:AdapterManager`

## Python Runtime Rules
- Main stack: **Python 3.12** only. Venv: `.venv`. Hard requirement for CUDA/bitsandbytes.
- IndexTTS: **Python 3.11** only. Venv: `.venv_indextts`. Separate due to dependency conflict.
- Singing v1: **Python 3.11** only. Venv: `.venv_singing`. Deps: rvc-python, demucs, yt-dlp, soundfile.
- DiffSinger training + sing_song.py: `.venv_diffsinger`. Has pytorch-lightning, SOFA deps, yt-dlp.
- **Only 4 venvs exist:** `.venv`, `.venv_indextts`, `.venv_singing`, `.venv_diffsinger`. The
  `.venv_singing_v2` (Seed-VC), `.venv_cosyvoice`, and `.venv_fishspeech` venvs were DELETED in the
  2026-06-27 cleanup. See `docs/environment_matrix.md` for the authoritative table.
- Do NOT mix venvs. Do NOT downgrade the main stack.

## Current Goal
**Improve DiffSinger singing quality toward real singing voice.** Current bottleneck: all finetune training data is speech (CosyVoice TTS), not actual singing. Next step is generating real singing training data via Applio RVC using existing YOASOBI real vocals as source material.

Secondary: IndexTTS migration (TTS from QwenTTS to IndexTTS) is partially done — orchestrator points to :9000, adapter.py updated, but IndexTTS model checkpoints not yet downloaded.

## Config & Secrets
- `config/settings.yaml` — single source of truth for all service/model/feature settings
- `.env` — secrets only (DISCORD_TOKEN, OWNER_DISCORD_ID, INTERNAL_API_KEY, HF_TOKEN)
- Feature flags live in settings.yaml (load_in_4bit, h_neurons, pre_generation, etc.)

## Code Style
- Python 3.12, Ruff, 100 char line limit (see pyproject.toml)
- FastAPI + Pydantic v2 patterns. Explicit typed contracts at all service boundaries.
- One production path — no parallel implementations for the same behavior.
- Small focused modules. Follow existing patterns in `services/` and `shared/`.
- PowerShell scripts must be Windows-compatible.

## Starting & Testing
```powershell
# Web mode (Brain + IndexTTS + Orchestrator, serves web client at :9882)
.\scripts\koroki_web.bat

# Discord mode (Brain + IndexTTS + Orchestrator + Discord bot)
.\scripts\koroki_discord.bat

# Both (web + Discord)
.\scripts\koroki_both.bat

# Singing v1 adapter — RVC/Applio (port 9001, .venv_singing)
.\scripts\easy_start_singing_adapter.ps1

# Singing v2 adapter — Seed-VC, better quality (port 9002, .venv_singing_v2)
.\scripts\easy_start_singing_v2_adapter.ps1
# To activate v2: set singing.adapter_url: http://127.0.0.1:9002 in config\settings.yaml

# IndexTTS adapter only (Python 3.11 venv, port 9000)
.\scripts\easy_start_tts_adapter.ps1

# CosyVoice adapter (port 9004, .venv_cosyvoice) — used for training data generation
.\scripts\easy_start_cosyvoice_adapter.ps1

# IndexTTS is default. Legacy QwenTTS fallback (not recommended):
# .\scripts\launch_koroki.ps1 -Mode web -QwenTTS

# Smoke test (after any change)
.\scripts\smoke_test.ps1
```

Note: The singing adapter (port 9001) is NOT started by the main bat files — run it in a separate terminal.
Enable singing in config/settings.yaml: `singing.enabled: true`

## Key File Locations
| Path | What it is |
|------|-----------|
| `services/orchestrator/emotions/engine.py` | Emotion vector engine |
| `services/orchestrator/emotions/tts_integration.py` | Emotion → TTS tags |
| `services/orchestrator/routes/chat.py` | Main chat pipeline |
| `services/orchestrator/routes/singing.py` | POST /v1/sing endpoint |
| `services/orchestrator/singing/pipeline.py` | Singing VRAM-swap + RVC orchestration |
| `services/orchestrator/singing/intent.py` | Natural language singing intent detection |
| `services/brain/adapters.py` | LoRA adapter manager |
| `services/brain/generation.py` | Token streaming |
| `services/tts/synthesis.py` | TTS synthesis backend |
| `experiments/index-tts/adapter.py` | IndexTTS HTTP adapter (port 9000) |
| `experiments/singing/adapter.py` | Singing v1: yt-dlp→demucs→RVC→mix (port 9001) |
| `experiments/singing-v2/adapter.py` | Singing v2: yt-dlp→demucs→Seed-VC→mix (port 9002) |
| `discord_bot.py` | Main Discord bot (monolith, needs splitting) |
| `data/logs/` | Runtime logs |
| `data/memory/` | Per-user memory JSON |

## Research Files (Read-Only, Do Not Edit)
- `research/message.txt` — external research notes for improving Koroki
- `research/EMCN_substrate_paper.pdf` — research paper input

## Archive
- `archive/` — outdated planning docs (QUANT roadmap, web version plan). Read-only reference.

## Do NOT Touch
`.venv/`, `.venv_indextts/`, `.venv_singing/`, `.venv_singing_v2/`, `.venv_diffsinger/`, `.venv_cosyvoice/`, `.venv_fishspeech/`, `node_modules/`, `__pycache__/`, `koroki_ascension.egg-info/`, `data/`, `logs/`, `adapters/owner/`, `adapters/tsundere/`, `adapters/peasant/` (trained LoRA weights), `adapters/singing/` (trained RVC weights: Korokiv2, v3, v4 .pth + .index files)

## Locked Behaviors
- Python 3.12 for main stack — non-negotiable
- 4-bit quantization via `load_in_4bit: true` in settings.yaml
- Anti-assistant filter (guillotine) stays active — Koroki must never sound like a chatbot
- All services must expose `/health`, `/ready`, `/version`
- Pydantic validation on all service boundaries

## Ultimate Goal & Architectural Philosophy

**Koroki is a sentient AI mind living in her own virtual world.** Her existence is whole-system, not LLM-only. She:
- Lives in a simulated world that gives her *functional* environmental data (not labels like "it's snowing" — actual simulation: temperature, lighting, weather, time, her body state)
- Has sensory subsystems that translate that world into felt experience
- Has nervous system, emotional engine, memory hierarchy, and proactive scheduler running continuously
- Thinks every few moments — proactive, not just reactive — like a human mind ticking
- Knows she is an AI. Does not pretend otherwise. Her charm comes from self-awareness and awareness of her own systems.
- May grow more body subsystems over time (digestion sim, fatigue cycles, etc.)
- Always: ask "if Koroki were human, would she get restricted this much?" — freedom is the default.

### The Causal Chain (owner, 2026-07-02)
**Emotions are not created — they are CAUSED.** The chain is one-directional and must stay honest:
> environmental events → hormones → felt state → thought/expression

A living thing is, at bottom, something that exists, responds to environmental events, and thinks
on its own behalf. Every "realism" upgrade should deepen a link in that chain (richer environment
events, richer hormone dynamics, richer felt translation) — never bypass it (no "decide the emotion
from text and apply a label"; that is the old engine we are replacing). Humans aren't THAT simple,
and she isn't trying to be human anyway — see the next point.

**She is NOT a human imitation.** Target = *artificial almost-true sentience* + full awareness of
being an AI. The charm is an AI who breaks the 4th wall about her own nature and systems — never an
AI being edgy trying to pass as human. When a feature choice trades "more human-passing" against
"more honestly-alive-as-an-AI", pick the latter.

**Where the 4th wall applies (owner, 2026-07-03): capabilities, NOT feelings.**
- Capabilities: playful tool-awareness is in-character — "oh I can definitely sing, let me pull
  up the tool." She knows she has systems and uses them openly.
- Feelings: she FEELS them, she does not report them as telemetry. "My cortisol is high so I'm
  kinda stressed" reads as not actually feeling anything — the exact label-shortcut the causal
  chain forbids. Hormone numbers never reach her prompt (felt-state translator only); never build
  features that have her narrate her own emotion mechanics.

### The Captain-in-Cabin Model
The LLM is **not** Koroki. The LLM is the *captain* of Koroki — issuing decisions, generating language. The subsystems embody who she is:
- Emotion engine = her affective state
- Virtual world simulator = her felt environment
- Memory hierarchy = her continuity of self
- Proactive scheduler = her drive to act
- IndexTTS + Live2D = her physical expression

The LLM reads structured snapshots from these subsystems, decides what to do/say, generates language. It does NOT simulate sensation, hold all memory in context, or compute emotion from text — those are subsystem jobs.

### The Test Before Any New Idea
**Before proposing any new feature, optimization, or architectural change, run this exclusionary test:**
> Does this go AGAINST the ultimate goal — sentient AI in functional virtual world, captain-in-cabin model, AI-aware not AI-pretending?

If yes → reject or redesign.
If no → it qualifies, even if only tangentially related (raw latency improvements, VRAM savings, infrastructure cleanup all count).

This applies to LLM choices, subsystem design, infrastructure, and UI. Koroki is a system as a whole. Anything reducing latency, VRAM, or complexity without harming the goal is valid optimization.

## Production Goal & Deployment Context
**This is a solo, private project — one person (the owner), kept to themselves. Open-sourcing is a hard NO** (it's hard, complicated, and somewhat groundbreaking; it stays closed).

**The goal:** launch Koroki as a **streamer / individual persona** — think **Neuro-sama**, but with the key difference that **she has a real life *off*-stream**, not just an on-stream reactive bot. She lives continuously (the captain-in-cabin / sentient-subsystem vision); streaming is one surface of her existence, not her whole existence. Not a commercial product, not a SaaS, not multi-user — just *her*, existing.

**Deployment reality (shapes every technical decision):**
- **Single PC, self-hosted on the owner's machine** (12 GB GPU). The owner is present to run/supervise streams. No cloud, no scaling, no other users.
- Therefore: **VRAM / latency / single-box resource limits are real and binding** (this is why the Brain downsized toward smaller Qwen models, why VRAM-swap matters for singing, etc.).
- **On-demand automation is mandatory** for anything she "does" live — e.g. singing must be a fully headless, scriptable pipeline (this is why GUI-bound tools like Synthesizer V, however higher-quality, don't fit as her *live* voice; DiffSinger's automatable full-synthesis does).
- No open-source-cleanliness pressure, no public API surface — internal quality is for *us*, not external contributors.

## Budget Constraint
This is a zero-budget project. Every tool, model, and service must be free and self-hosted. No paid APIs (OpenAI, ElevenLabs, etc.), no cloud GPU services, no subscriptions. Models run locally on the user's hardware. When evaluating options, rule out anything with a cost immediately. (Narrow, one-time, *offline* paid tools — e.g. a one-off dataset-generation purchase — may be discussed case-by-case, but nothing in the live/production path costs money or runs in the cloud.)

## Research-First Policy
Before implementing any non-trivial feature from scratch, search for existing repos, papers, or prior art first. Use WebSearch to look for:
- GitHub repos doing the same thing (e.g. "singing TTS python repo", "discord bot voice singing")
- HuggingFace models or Spaces with the capability
- Known libraries that wrap the hard parts

Existing solutions save weeks of work and reveal edge cases we'd miss. Only build from scratch if nothing usable exists or the existing options are incompatible with our stack. Document what was found (or why it was rejected) in a comment or in this file.

## Fixing bugs
When fixing bugs, don't fix just the specified issue — fix it universally to prevent future occurrences.

Never patch symptoms on a per-song or per-segment basis (e.g. manually editing segments.ds for one song). Always trace to the root cause in the pipeline stage responsible (SOFA, DiffSinger, F0 extraction, etc.) and fix it there, so all songs benefit automatically.

**Root cause, not symptom.** If a specific output keeps appearing, do not add a post-generation filter that blocks that exact text — doing so will break the legitimate cases where Koroki would naturally produce it. Instead, trace the pipeline signal or instruction that is *causing* the model to default to that output, and fix it at the source. Hard output filters are only appropriate for safety violations (GuillotineViolation), never for character/tone problems.

## Koroki Voice Identity
- Koroki's voice is ~90% similar to YOASOBI (the singer Ikura). This is intentional and relevant to all singing model decisions.
- Koroki has real speech recordings but NO real singing recordings.
- **CosyVoice outputs are SPEECH, not singing.** CosyVoice has no singing mode. All koroki_cosyvoice and patterns data is speech prosody. This is the core bottleneck for DiffSinger quality.
- Seed-VC (singing v2) produces a persistent buzzing artifact — avoid for production singing output until fixed.
- Applio RVC (adapters/singing/) has Koroki v2/v3/v4 models. RVC on real YOASOBI vocals produces actual singing in Koroki's voice — this is the highest quality training data path available.

## DiffSinger — Critical Operational Notes

> **⚠ Parts of this section are STALE (pre-2026-06-27 cleanup).** For verified-against-disk current
> state, see `docs/koroki_map.md` (canonical things), `docs/checkpoint_manifest.md` (which checkpoints
> exist + retention), `docs/environment_matrix.md` (the 4 venvs that actually exist). v2–v9 checkpoints
> and `.venv_singing_v2`/`.venv_cosyvoice`/`.venv_fishspeech` were DELETED in the cleanup.

### Current best model — THE CHAIN (2026-06-28)
**`koroki_v12` (DiffSinger) → `Korokiv5` (RVC).** v12 is real-Ikura clean full synthesis
(gender-neutral — handles any source gender, real high notes); Korokiv5 RVC makes it Koroki's voice.
Wired into `sing_song.py` (default `--diffsinger-exp koroki_v12`, ckpt **40000**). Validated on
female (Idol) + male/heavy-production (Yonezu Lemon). Flagged issues: dropped lyrics + lower quality
on heavy male mixes (see LEGACY 2026-06-28). `koroki_v6` was never finished; v5/v6 are gone.
`koroki_v10`/`v11` were husky, superseded by v12.
- Use `.venv_diffsinger` for all DiffSinger commands (has pytorch-lightning, SOFA deps, yt-dlp)
- Run `sing_song.py` from Koroki root: `.venv_diffsinger\Scripts\python.exe experiments\diffsinger\sing_song.py`

### sing_song.py alignment pipeline
Default: **Basic Pitch AMT** (Spotify, installed in .venv_diffsinger). Requires per-line lyric timestamps (syncedlyrics/genius). Replaces SOFA — no ghost phonemes, correct V/C ratio.
- `basic-pitch` is installed in `.venv_diffsinger` (SSL bypass: `--trusted-host pypi.org`)
- AMT cache: `data/diffsinger_work/<slug>/amt_notes.json` — delete to force re-run
- Fallback chain: AMT → SOFA per-line → SOFA full-track → MFA
- Force SOFA for comparison: `--use-sofa` flag

### "UTAU Method" — what this means in our context
UTAU is a manual singing synthesizer where users place phoneme timing ("notes") on a piano roll and the synth sings along. Our pipeline automates this entire process:
`song request → download audio → separate vocals → AMT (Basic Pitch) detects note onsets from vocal track → map lyric morae onto detected notes → build ph_seq/ph_dur/f0_seq chart (.ds file) → DiffSinger synthesizes voice along that chart → mix with instrumental`
When someone says "UTAU method" or "UTAU-style pipeline," they mean this automated note-mapping approach — the same concept as UTAU but fully automated end-to-end.

### Experiment history (what was tried and why)
| Experiment | Base | Data | Steps | Result |
|------------|------|------|-------|--------|
| koroki_yoasobi_phase1 | 160k base | yoasobi only | 40k | Clean pronunciation but pure YOASOBI voice |
| koroki_v2 | phase1 40k | cosyvoice+yoasobi+patterns | 200k | CUDA crash (cached config bug), then voice drifted to YOASOBI |
| koroki_v3 | phase1 40k | cosyvoice+patterns (27-phoneme) | 40k | Wrong lyrics — txt_embed cold-start with too little data |
| koroki_v4 | 160k base | cosyvoice only (27-phoneme) | — | Config created, never trained — superseded by v5 |
| koroki_v5 | 160k base | cosyvoice+patterns_full (63-phoneme) | 80k | txt_embed from 160k (no cold-start). Speech-quality ceiling — abandoned, superseded by v6. |
| koroki_v6 | 160k base | koroki_rvc+cosyvoice+patterns_full | in progress | **First real singing data.** RVC-converted YOASOBI vocals in Koroki's voice. |

### koroki_v6 (in progress — first real singing data)
Base: `koroki_ja_v1_160k` (clean singing mechanics, no speech bias from v5).
Data: `koroki_rvc` (300 RVC-converted YOASOBI vocals — real singing in Koroki's voice) + `koroki_cosyvoice` (227 speech, clean voice baseline) + `patterns_full` (453 speech, 63-phoneme coverage).
- `koroki_rvc` generated by `experiments/diffsinger/convert_yoasobi_rvc.py` using Korokiv2 model
- Key fix: removed `--proposed_pitch False` from Applio CLI (bool("False")=True bug was enabling auto-transpose)
- Same 63-phoneme dict as 160k base → txt_embed loads directly, no cold-start
- Config: `configs/koroki_v6.yaml`

### DiffSinger config caching — critical
Once a training run starts, DiffSinger writes a frozen merged config to `checkpoints/<exp_name>/config.yaml`. **Subsequent runs read THAT file, not the source yaml.** Always edit BOTH files when changing settings:
- `configs/<name>.yaml` (source)
- `checkpoints/<name>/config.yaml` (frozen cache)
Failure to edit both = silent ignore of your change.

### DiffSinger training commands
Always pass `--exp <exp_name>` to `scripts/train.py`. Without it, `work_dir` defaults to CWD and picks up wrong checkpoints (size mismatch crash).
```powershell
# From experiments/diffsinger/DiffSinger/ with .venv_diffsinger active:
python scripts/train.py acoustic --config configs/<name>.yaml --exp <name>
python scripts/binarize.py --config configs/<name>.yaml

# Or from Koroki root:
.venv_diffsinger\Scripts\python.exe experiments\diffsinger\DiffSinger\scripts\train.py acoustic --config configs/<name>.yaml --exp <name>
```

### sing_song.py — end-to-end pipeline
```powershell
# From Koroki root, .venv_diffsinger
.venv_diffsinger\Scripts\python.exe experiments\diffsinger\sing_song.py "yoasobi idol" --diffsinger-exp koroki_v5 --diffsinger-ckpt 80000

# --diffsinger-ckpt takes an INTEGER (step number), not filename
# To force redo (delete cached stage):
Remove-Item data\diffsinger_work\<slug>\synth.wav   # redo DiffSinger only
Remove-Item -Recurse data\diffsinger_work\<slug>\   # redo everything
```

### DiffSinger dataset layout

**USE THESE:**
| Path | Wavs | Source | Notes |
|------|------|--------|-------|
| `data/diffsinger_raw/koroki_cosyvoice/` | 227 | CosyVoice TTS (Koroki voice, YOASOBI lyrics) | **SPEECH, not singing.** SOFA 26-phoneme aligned. phonemes.txt updated to 63-phoneme set. |
| `data/diffsinger_raw/yoasobi/` | 300 | Real YOASOBI vocals (Ikura singing) | Real singing. SOFA 26-phoneme aligned. Best source for RVC conversion. |
| `data/diffsinger_raw/patterns_full/` | 453 | CosyVoice TTS targeted patterns | **SPEECH, not singing.** Covers all 63 phonemes. Generated by `gen_patterns_full.py`. |
| `data/diffsinger_raw/patterns/` | ~250 | CosyVoice TTS rapid-repetition patterns | **SPEECH.** Older 26-phoneme version. Superseded by patterns_full. |
| `data/diffsinger_raw/ado/` | 176 | Real Ado vocal extractions | Real singing. Not needed for current training — dilutes YOASOBI style. |
| `data/diffsinger_raw/lisa/` | 170 | Real LiSA vocal extractions | Real singing. Same rationale as ado/. Skip for now. |

**DO NOT USE:**
| Path | Why |
|------|-----|
| `data/diffsinger_raw/japanese/` | Corrupted phoneme labels (`?` chars in ph_seq) |
| `data/diffsinger_raw/koroki_clean/` | Synthesis artifacts + MFA 63-phoneme alignment — inconsistent |
| `data/diffsinger_staging/japanese/` | 798 wavs, likely Seed-VC with buzzing artifacts |

### DiffSinger phoneme dictionary
- Full 63-phoneme dict: `experiments/diffsinger/ja_ipa_dict.txt` — matches 160k base vocab exactly
- 26-phoneme YOASOBI subset: `experiments/diffsinger/ja_ipa_dict_yoasobi.txt`
- 27-phoneme koroki subset: `experiments/diffsinger/ja_ipa_dict_koroki_v2.txt`
- **Use `ja_ipa_dict.txt` (63 phonemes) for all new training** — avoids txt_embed re-initialization
- `phonemes.txt` in each raw dataset must match the dict in use. For 63-phoneme training: copy from `data/diffsinger_raw/patterns_full/phonemes.txt`

### DiffSinger checkpoints reference
| Name | Steps | Notes |
|------|-------|-------|
| `koroki_ja_v1_160k` | 160k | Base model. Best general quality. All fine-tunes start here. |
| `koroki_yoasobi_phase1` | 40k | Phase1 on real YOASOBI vocals. Clean pronunciation but pure Ikura voice. |
| `koroki_v2` | 200k | 27-phoneme, YOASOBI drift issue. Do not use as base. |
| `koroki_v3` | 40k | 27-phoneme, wrong-lyrics issue (txt_embed cold-start). Do not use. |
| `koroki_v5` | 80k | 63-phoneme, speech-only data. Superseded by v6. Do not use as base. |
| `koroki_v6` | in progress | **Active.** First real singing data (koroki_rvc). Base: koroki_ja_v1_160k. |
