# Known-Good Dependency Stacks

> **Why this exists:** we've repeatedly lost time to the pyworld/numpy/torch/lightning version
> dance. This records the **exact package versions that are currently installed AND working** in
> each venv, plus the load-bearing constraints (the pins you must NOT casually bump). If you're
> about to `pip install -U` something in one of these venvs, check here first.
>
> Verified against the live venvs: **2026-06-28.** Companion: `docs/environment_matrix.md` (which
> venv runs what), `scripts/doctor.ps1` (fast health check).

---

## `.venv` — main stack (Python 3.12.10) — Brain / Orchestrator / TTS / LoRA training

| Package | Version | Note |
|---|---|---|
| torch | **2.10.0+cu128** | CUDA 12.8 build. `doctor.ps1` confirms `cuda.is_available()`. |
| numpy | **2.4.3** | numpy 2.x is fine here (unlike indextts/singing). |
| transformers | 4.57.3 | |
| peft | 0.18.1 | |
| trl | 0.24.0 | LoRA SFT — `SFTTrainer`/`SFTConfig` API as used in `scripts/train_lora_4b.py`. |
| bitsandbytes | 0.49.2 | 4-bit NF4 quantization. The reason the stack is hard-pinned to Python 3.12. |
| datasets | 4.3.0 | |
| accelerate | 1.12.0 | |

**Load-bearing:** Python 3.12 + CUDA torch + bitsandbytes is the non-negotiable core (CLAUDE.md).
A clean 2026-06-28 LoRA retrain ran on exactly this stack (5 epochs, ~25 min, loss 0.255). Do not
downgrade.

---

## `.venv_diffsinger` — DiffSinger training + `sing_song.py` (Python 3.11.9)

| Package | Version | Note |
|---|---|---|
| torch | **2.8.0+cu128** | ⚠ **Deliberately lower than the main stack's 2.10.** DiffSinger + the NSF-HiFiGAN vocoder are validated on 2.8. Don't bump to match `.venv`. |
| numpy | 2.4.6 | |
| pytorch-lightning | **2.3.3** | DiffSinger's trainer targets the Lightning 2.3.x API. Newer Lightning has broken `Trainer` kwargs before. Pin. |
| lightning | 2.3.3 | keep in lockstep with pytorch-lightning. |
| pyworld | **0.3.5** | F0/aperiodic extraction for variance embeds. Version-sensitive against numpy — this combo works. |
| onnxruntime | 1.26.0 | |
| onnxruntime-gpu | 1.25.1 | used by SOFA / separation models. |
| basic-pitch | **0.4.0** | AMT note detection (the default align path). Installed via SSL bypass (`--trusted-host pypi.org`). |
| demucs | 4.0.1 | vocal separation. |
| yt-dlp | 2026.3.17 | song download. Keep reasonably fresh (YouTube breaks old yt-dlp). |
| librosa | 0.11.0 | |

**Load-bearing:** torch 2.8 (NOT 2.10) + lightning 2.3.3 + pyworld 0.3.5. This is the trio that has
broken before. The whole DiffSinger→RVC chain (koroki_v12) runs on this venv.

---

## `.venv_indextts` — IndexTTS adapter :9000 (Python 3.11.9)

| Package | Version | Note |
|---|---|---|
| torch | 2.10.0+cu128 | |
| numpy | **1.26.2** | ⚠ **numpy <2 required.** IndexTTS deps are not numpy-2 clean. Do not bump to 2.x. |
| transformers | **4.52.1** | ⚠ Pinned older than the main stack (4.57.x). IndexTTS's model code targets this API. |
| accelerate | 1.8.1 | |
| librosa | 0.10.2.post1 | older than the 0.11 used elsewhere — leave it. |

**Load-bearing:** numpy 1.26.x + transformers 4.52.1. This is why IndexTTS gets its **own** venv (the
numpy-2 / transformers-4.57 main stack is incompatible).

---

## `.venv_singing` — Singing v1 RVC/Applio adapter :9001 (Python 3.11.9)

| Package | Version | Note |
|---|---|---|
| torch | **2.11.0+cpu** | ⚠ **CPU-only build (confirmed `cuda.is_available()==False`).** RVC in this venv runs on CPU = slow. This is acceptable because the **production singing chain does NOT use this venv** — it uses Applio's OWN bundled env (`ApplioV3.6.2/env/`). `.venv_singing` only backs the experimental v1 RVC adapter (:9001). If you ever make :9001 production, install a cu128 torch here first. |
| numpy | **1.23.5** | ⚠ **Old numpy required by fairseq.** Do not bump. |
| fairseq | **0.12.2** | ⚠ The classic RVC pain dependency. Pins numpy down to 1.23.x and is fussy about torch. This exact combo works — leave it frozen. |
| pyworld | 0.3.5 | |
| demucs | 4.0.1 | |
| yt-dlp | 2026.6.9 | |
| librosa | 0.11.0 | |

**Load-bearing:** fairseq 0.12.2 + numpy 1.23.5. This is the most fragile venv — fairseq has no
modern release and constrains everything around it. Treat as frozen; if it breaks, rebuild from a
fairseq-0.12.2-compatible lockfile rather than upgrading piecemeal.

---

## General rules

1. **Never `pip install -U` blindly in these venvs.** Each is a balanced point. Bumping one package
   (esp. numpy, torch, lightning, transformers, fairseq) cascades.
2. **numpy is the usual culprit.** Main stack + diffsinger are numpy 2.x; indextts + singing are
   numpy 1.x. Mixing is why they're separate venvs.
3. **torch is pinned per-venv on purpose** (2.10 main/indextts, 2.8 diffsinger, 2.11 singing). They do
   NOT have to match.
4. After any dependency change, run `scripts/doctor.ps1` and a smoke test for that subsystem.
5. If you must change a pin, record the new known-good combo here with the date.
