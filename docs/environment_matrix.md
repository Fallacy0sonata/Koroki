# Environment Matrix — venvs, Python versions, what runs where

> **Why this exists:** "which Python / which venv for what" is a recurring time-sink, and a
> CUDA-torch-CPU mismatch has bitten us before. This is the authoritative venv table.
>
> Last verified against the filesystem: **2026-06-28.** CLAUDE.md lists 7 venvs; only **4 exist**
> (the other 3 were deleted in the 2026-06-27 cleanup). Trust this table.

## The four venvs that actually exist

| Venv | Python | Used by | Start script | Status |
|---|---|---|---|---|
| `.venv` | **3.12.10** | Main stack: Brain (:9881), Orchestrator (:9882), TTS (:9880), LoRA training, discord_bot | `scripts/koroki_web.bat`, `koroki_discord.bat`, `koroki_both.bat` | **LIVE.** Hard 3.12 requirement (CUDA/bitsandbytes). Never downgrade. |
| `.venv_indextts` | **3.11.9** | IndexTTS adapter (:9000) | `scripts/easy_start_tts_adapter.ps1` | **LIVE.** Separate due to dependency conflict with main stack. |
| `.venv_diffsinger` | **3.11.9** | DiffSinger training + `sing_song.py` (the singing chain) | `scripts/easy_start_singing_diffsinger_adapter.ps1` | **LIVE.** Has pytorch-lightning, SOFA, basic-pitch, yt-dlp. |
| `.venv_singing` | **3.11.9** | Singing v1 — RVC/Applio adapter (:9001) | `scripts/easy_start_singing_adapter.ps1` | Experimental. Standalone RVC cover pipeline (not the DiffSinger chain). |

## Deleted venvs (2026-06-27 cleanup) — CLAUDE.md still lists these, they're GONE

| Venv | Was for | Why removed |
|---|---|---|
| `.venv_singing_v2` | Seed-VC singing v2 (:9002) | Persistent buzzing artifact — pipeline abandoned. |
| `.venv_cosyvoice` | CosyVoice adapter (training-data gen) | Data-gen done; CosyVoice output is speech, not singing. |
| `.venv_fishspeech` | Fish Speech | Abandoned TTS experiment. |

## Rules (from CLAUDE.md — still binding)

- **Do NOT mix venvs.** Each service imports from its own.
- **Do NOT downgrade the main stack** off Python 3.12.
- Main stack needs CUDA-enabled torch. If you see torch running on CPU, the wrong wheel got
  installed into `.venv` — reinstall the cu128 build, don't work around it.
- Applio (RVC) has its **own** bundled Python at `ApplioV3.6.2/env/python.exe` — it is not one of
  the four venvs above. `sing_song.py`'s RVC chain shells out to it directly.

## Port map (cross-reference)

| Port | Service | Venv |
|---|---|---|
| 9882 | Orchestrator | `.venv` |
| 9881 | Brain (Qwen3-1.7B + koroki_4b LoRA) | `.venv` |
| 9880 | TTS (QwenTTS legacy) | `.venv` |
| 9000 | IndexTTS (primary speech) | `.venv_indextts` |
| 9001 | Singing v1 — RVC/Applio | `.venv_singing` |
| 9003 | Singing v3 — DiffSinger adapter | `.venv` adapter + `.venv_diffsinger` for `sing_song.py` |

(Ports 9002 / Seed-VC v2 and 9004 / CosyVoice are retired — their venvs are deleted.)
