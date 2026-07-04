# Project Guidelines

## Architecture
- Koroki Ascension 2.0 is a Discord-first system with three Python FastAPI services: orchestrator on 9882, brain on 9881, and TTS on 9880.
- Treat the orchestrator as the single entry point for routing, validation, and streaming. Keep model loading and adapter swaps in brain, and synthesis in TTS.
- Use [config/settings.yaml](config/settings.yaml) as the shared source of truth for service, model, memory, telemetry, and feature settings.
- Prefer the existing docs and scripts over restating setup logic. The main entry points are [README.md](README.md), [NEXT_STEPS.md](NEXT_STEPS.md), and [scripts/dev_start.ps1](scripts/dev_start.ps1).

## Code Style
- Target Python 3.12 and follow the existing Ruff settings in [pyproject.toml](pyproject.toml) with a 100 character line limit.
- Match the current FastAPI and Pydantic patterns in [services/](services/) and [shared/](shared/): explicit contracts, typed boundaries, and small focused modules.
- Keep PowerShell scripts Windows-compatible and consistent with the existing startup flow in [scripts/dev_start.ps1](scripts/dev_start.ps1).
- Follow the existing npm workspace layout for JavaScript work under [clients/](clients/).

## Build and Test
- Set up Python with `py -3.12 -m venv .venv`, then activate `.venv\Scripts\Activate.ps1` and run `pip install -e .`.
- Start the local stack with `./scripts/dev_start.ps1` and validate it with `./scripts/smoke_test.ps1`.
- Start the Discord bot with `npm run bot:start` from the repo root, or run `npm install` and `npm start` in [clients/discord-bot](clients/discord-bot).
- Start the web client with `npm run web:start` from the repo root.

## Conventions
- Do not edit generated, vendored, or runtime-state directories such as [.venv/](.venv/), [node_modules/](node_modules/), [__pycache__/](__pycache__/), [koroki_ascension.egg-info/](koroki_ascension.egg-info/), [logs/](logs/), or [data/](data/) unless the task explicitly targets them.
- Keep a single production path. Avoid adding parallel implementations for the same service behavior.
- Link to the existing phase and setup docs instead of copying them into new files.
- Preserve the locked behavior documented in [README.md](README.md) and [config/settings.yaml](config/settings.yaml), especially the Python 3.12 runtime requirement, 4-bit gating, and adapter-based persona routing.
