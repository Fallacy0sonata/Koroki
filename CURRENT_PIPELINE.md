# Koroki — Current Pipeline Map
> **Last updated:** 2026-03-30
> **Purpose:** Single source of truth for the project's architecture, services, file locations, and active features. Update this every time a feature ships or a major change is made.

---

## 1. Service Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Launcher (bat)                       │
│  scripts/koroki_web.bat                                 │
│  scripts/koroki_discord.bat                             │
│  scripts/koroki_both.bat                                │
│           │                                             │
│           ▼                                             │
│  scripts/launch_koroki.ps1  ← unified orchestrator      │
└─────────┬───────────────────────────────────────────────┘
          │
          ├──► Brain (:9881)      services/brain/app.py
          ├──► TTS (:9880)        services/tts/app.py
          ├──► Orchestrator (:9882) services/orchestrator/app.py
          │        ├── Auth routes    (/v1/auth/*)
          │        ├── Chat routes    (/v1/chat)
          │        ├── Stream routes  (/v1/stream)
          │        ├── Voice routes   (/v1/voice)
          │        ├── Health routes  (/health, /ready, /version)
          │        ├── Log routes     (/v1/logs, /v1/logs/list)   ← NEW
          │        └── Web frontend   (/ → clients/web/)
          │
          └──► Discord Bot         discord_bot.py
```

## 2. Frontend (Web Client)

| File | Purpose |
|------|---------|
| `clients/web/index.html` | Main HTML shell, Live2D canvas, composer, menu drawer, dev console |
| `clients/web/app.js` | All client logic: auth, chat, voice, Live2D, lip sync, dev console |
| `clients/web/styles.css` | Full styling including dev console terminal theme |

### Dev Console (localhost-only)
- Toggle button `</>` appears bottom-left only on `127.0.0.1` / `localhost`
- Floating, draggable, resizable terminal window
- Tabs: `orchestrator \| brain \| tts \| discord \| all`
- Configurable tail lines (default 200, max 5000)
- Auto-refresh every 5 seconds (toggleable)
- Auto-scroll toggle

## 3. Backend Services

### 3a. Brain (`services/brain/`)
| File | Purpose |
|------|---------|
| `app.py` | FastAPI app, `/health`, `/v1/generate`, WS `/ws/stream` |
| `adapters.py` | LoRA adapter manager (owner/tsundere/peasant), 4-bit quant |
| `generation.py` | Token streaming logic |
| `prompt_builder.py` | Prompt assembly from config + persona templates |

- Logs to: `data/logs/brain.log` AND stdout
- Model: configurable via `BRAIN_MODEL_PROFILE` (production=Qwen2.5-3B, staging=Qwen3-8B)
- VRAM cap: `models.brain.max_memory_gib: 8` in config

### 3b. TTS (`services/tts/`)
| File | Purpose |
|------|---------|
| `app.py` | FastAPI app, `/health`, `/v1/synthesize`, `/v1/profiles` |
| `synthesis.py` | FasterQwen3 backend, clone profiles, SoX post-processing |
| `voice_profiles.py` | Voice style builder |

- Logs to: `data/logs/tts.log` AND stdout
- Clone profiles: `sultry_sexy_flirty`, `sassy_regal`
- Backend: FasterQwen3 (SDPA attention), fallback to qwen_tts

### 3c. Orchestrator (`services/orchestrator/`)
| File/Dir | Purpose |
|----------|---------|
| `app.py` | FastAPI app, middleware, static file serving |
| `routes/auth.py` | Login, signup, session, logout |
| `routes/chat.py` | Main chat endpoint, pipeline coordination |
| `routes/stream.py` | WebSocket streaming |
| `routes/voice.py` | TTS synthesis endpoint |
| `routes/health.py` | Health/readiness/version checks |
| `routes/log.py` | **NEW** — Log retrieval endpoints |
| `schemas.py` | Pydantic models |
| `guards/` | Guillotine (forbidden token filter) |
| `memory/` | Memory management |
| `telemetry/` | Request tracing |

- Logs to: `data/logs/orchestrator.log` AND stdout
- Serves web client at `/`
- Serves static assets at `/assets`

### 3d. Discord Bot (`discord_bot.py`)
- Logs to: `data/logs/discord.log` AND stdout
- Routes messages to Orchestrator `/v1/chat`
- Supports slash commands and prefix commands
- Deferred TTS via `/v1/voice`

## 4. Adapters (LoRA Fine-tunes)

| Adapter | Path | Usage |
|---------|------|-------|
| Owner | `adapters/owner/` | `is_owner=True` → deeply affectionate, no assistant tone |
| Tsundere | `adapters/tsundere/` | `relationship_score >= 50` → playful, teasing |
| Peasant | `adapters/peasant/` | `relationship_score < 50` → formal, cold, distant |

Selection logic in `services/brain/adapters.py:AdapterManager`

## 5. Configuration

| File | Purpose |
|------|---------|
| `config/settings.yaml` | Master config: services, models, adapters, memory, telemetry, discord |
| `.env` | Secrets: DISCORD_TOKEN, OWNER_DISCORD_ID, ORCHESTRATOR_URL, etc. |
| `.env.example` | Template for .env |

### Key Settings
- Brain VRAM cap: `models.brain.max_memory_gib: 8`
- Brain 4-bit: `models.brain.load_in_4bit: true`
- Discord max tokens: `models.brain.discord_max_new_tokens: 80`
- Web max tokens: `models.brain.web_max_new_tokens: 64`
- Anti-assistant terms blocked in config under `models.brain.anti_assistant_terms`

## 6. Data Directories

| Path | Contents |
|------|----------|
| `data/logs/` | Runtime logs (orchestrator.log, brain.log, tts.log, discord.log) + telemetry JSON |
| `data/memory/` | Per-user memory JSON files |
| `logs/` | Training logs (train_owner.log, train_tsundere.log, train_peasant.log) |
| `voice_samples/` | Reference audio for TTS cloning |

## 7. Launcher Scripts

| File | Mode | Services Started |
|------|------|-----------------|
| `scripts/koroki_web.bat` | web | Brain + TTS + Orchestrator → http://127.0.0.1:9882 |
| `scripts/koroki_discord.bat` | discord | Brain + Orchestrator + Discord Bot |
| `scripts/koroki_both.bat` | both | Brain + TTS + Orchestrator + Discord Bot |

The bat files call `scripts/launch_koroki.ps1` which handles port clearing, env setup, Ollama auto-detect, and process lifecycle.

## 8. Change Log (Recent)

### 2026-03-30 — Dev Console + File Logging
- Added floating dev console to web client (localhost only)
- Added `/v1/logs` and `/v1/logs/list` API endpoints
- Added `FileHandler` to all 4 services for runtime log files
- Created `CURRENT_PIPELINE.md` (this file)

---

*When you add a feature or change architecture, update this file immediately.*
