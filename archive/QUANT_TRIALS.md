# Quant Trial Flow

## Purpose

Run repeatable web-side Brain/TTS experiments without touching the working Discord path.

## Recommended Baselines

### 1. Current web baseline

- Brain profile: `production`
- Meaning: current lighter web setup

Run:

```powershell
.\scripts\koroki_web.bat
.\scripts\benchmark_quant_trial.ps1 -Label web_baseline_production
```

### 2. Strong style reference

- Brain profile: `staging`
- Meaning: stronger current Qwen3-8B style reference

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\launch_koroki.ps1 -Mode web -BrainProfileOverride staging
.\scripts\benchmark_quant_trial.ps1 -Label web_style_reference_staging
```

## First Quantized Candidate

Once a quantized Brain runtime exists, compare it against the two baselines above.

Suggested labels:

- `web_quant_candidate_1`
- `web_quant_candidate_2`

## What To Compare

Use the JSON output under `data/logs/` and score:

- `brain_ttft_ms`
- `chat_elapsed_ms`
- `voice_elapsed_ms`
- `gpu_snapshot.used_mib`
- style quality
- assistantness leak
- owner warmth / regal tone

## Rule

Do not promote any quantized Brain to Discord until:

- style is still recognizably Koroki
- web latency is clearly better
- Brain and TTS coexist comfortably on the card
