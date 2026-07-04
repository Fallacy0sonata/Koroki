# Koroki Ascension 2.0

AI companion — Discord-first, Desktop-second.

## Architecture

```
Discord Bot (Node.js)
        │
        ▼
Orchestrator FastAPI :9882   ← single entry point, validates, routes, streams
        ├── Brain FastAPI :9881   ← Qwen2.5-3B + LoRA adapters, token streaming
        └── TTS FastAPI  :9880   ← Qwen3-TTS, audio synthesis
```

## Quick Start

### 1. Set up Python environment

Python 3.12 is required. Do not use 3.14 for the production environment.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

### 2. Configure

```powershell
Copy-Item .env.example .env
# Edit .env — set DISCORD_TOKEN, OWNER_DISCORD_ID, KOROKI_ROOT
```

### 3. Start all services

```powershell
.\scripts\dev_start.ps1
```

### 4. Smoke test

```powershell
.\scripts\smoke_test.ps1
```

### 5. Start Discord bot

```powershell
cd clients/discord-bot
npm install
npm start
```

## Service Endpoints

| Service      | Port | Health                        |
|--------------|------|-------------------------------|
| Orchestrator | 9882 | http://127.0.0.1:9882/health  |
| Brain        | 9881 | http://127.0.0.1:9881/health  |
| TTS          | 9880 | http://127.0.0.1:9880/health  |

Every service exposes `/health`, `/ready`, `/version`.

## Key Design Decisions (locked)

- **R01**: One production path — no dual implementations.
- **R03**: Strict Pydantic validation on all service boundaries.
- **R04**: `/health` + `/ready` + `/version` on every service.
- **R05**: Stage-level latency measured and logged per request.
- **Adapters**: owner / tsundere / peasant — loaded at startup, swapped cheaply at runtime.
- **H-Neurons**: Disabled Day 1. LoRA-first personality. Hooks available behind feature flag.
- **Pre-generation**: Slot architecture ready. Runtime generation disabled until Phase B.
- **4-bit quantization**: Disabled Day 1. Enable via `models.brain.load_in_4bit: true` in settings.yaml.
- **Python runtime**: Hard-pinned to Python 3.12 for CUDA, FlashAttention, and bitsandbytes stability.

## Directory Structure

```
config/             # settings.yaml — single source of truth
services/
  orchestrator/     # FastAPI :9882 — entry point, pipeline, streaming
  brain/            # FastAPI :9881 — LLM, adapters, generation
  tts/              # FastAPI :9880 — TTS synthesis, voice profiles
clients/
  discord-bot/      # Node.js discord.js bot
  desktop-app/      # Electron (Phase B)
shared/
  utils/            # config loader, ID generation
  contracts/        # API contracts (reference)
data/
  memory/           # user memory storage
  logs/             # telemetry JSON logs
scripts/            # dev_start, smoke_test
tests/
  contract/         # schema validation tests
  integration/      # end-to-end tests
```
