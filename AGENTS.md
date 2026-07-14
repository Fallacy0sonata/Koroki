# Koroki Agent Guide

This repository is the private working source for Koroki, a local-first AI companion with
Python services, Node clients, model/training pipelines, and a live Minecraft player.

## Read in this order

1. `README.md` for the public architecture and project intent.
2. `CLAUDE.md` when present for owner preferences and the project's decision philosophy.
3. The newest relevant entries in `LEGACY.md` for lessons and decisions, not as a current API map.
4. The current-position block in `docs/master_queue.md`, then the code and tests that implement it.

Private context files are intentionally ignored by Git. Code, tests, and the live configuration
win when an old document disagrees with the repository. Confirm a claimed gap in code before
building it; this project has previously duplicated finished systems because a queue entry drifted.

## Safety and privacy

- Assume Discord, Minecraft, and local model services may be live. Do not start, stop, restart,
  relog, or reconfigure them unless the task explicitly calls for it.
- Never print, commit, copy to a worktree, or publish `.env`, tokens, certificates, private keys,
  account identifiers, model weights, personal memory/data, or raw conversation/training corpora.
- Do not install third-party tools, connect accounts, push, publish, upload, or enable write-capable
  integrations without the owner's approval.
- Keep the canonical project private. A future portfolio/export should be a separate sanitized
  surface, not a reason to weaken the private workspace.
- Treat game input as a safety-sensitive boundary. Preserve focus confinement, purchase guards,
  owner-only controls, and fail-closed behavior.

## Architecture rules

- The orchestrator is the external boundary. Keep typed contracts between services.
- Keep one production implementation for each behavior; avoid shadow paths and speculative rewrites.
- Put deterministic policy in code. Use language/vision models for interpretation and expression,
  not for bypassing safety, accounting, navigation recovery, or state ownership.
- For Minecraft, optimize expedition progress and recovery, not the number of available verbs.
  Preserve failure causes, inventory accounting, reusable stations/storage, and task continuity.
- Keep GPU/model residency explicit. A larger pod improves reasoning capacity but does not replace
  reliable body mechanics, observability, or recovery policy.

## Efficient workflow

- Search with `rg`/`rg --files`; read only the relevant current code and nearby tests.
- Make the smallest coherent change. Add or update a focused test with behavioral changes.
- Run focused tests first, then the combined gate before handoff:

  ```powershell
  .\.venv\Scripts\python.exe scripts\check_all.py
  ```

- Python-only gate: `.\.venv\Scripts\python.exe scripts\check_all.py --python-only`
- Minecraft-only gate: `.\.venv\Scripts\python.exe scripts\check_all.py --minecraft-only`
- Staged secret scan: `.\.venv\Scripts\python.exe scripts\secret_scan.py`
- Python targets 3.12 and follows `pyproject.toml` Ruff settings. Do not mass-format legacy code.
- Minecraft is a separate package, not a root npm workspace; run its commands with
  `npm --prefix clients/minecraft-bot ...`.

## Git and worktrees

- The working tree can contain valuable uncommitted experiments. Never discard, overwrite, stage,
  or commit unrelated owner changes.
- Codex worktrees begin from Git state. A reliable committed source baseline is required before
  parallel worktrees can be trusted; untracked non-ignored source does not automatically follow.
- `.worktreeinclude` copies only selected ignored context/config files. It deliberately excludes
  `.env`, credentials, runtime data, model files, and large artifacts.
- Before a public push, inspect the exact diff and run a secret scan. Internal persona/history docs
  remain local even if the code later gets a sanitized portfolio mirror.

## Source map

- `services/`: Python brain, orchestrator, TTS, and vision services.
- `shared/`: typed contracts shared across service boundaries.
- `clients/discord-bot/`: Discord client.
- `clients/minecraft-bot/`: live Minecraft player and its standalone test suites.
- `clients/web/` and `clients/stage/`: web/world/avatar surfaces.
- `scripts/`: launch, evaluation, training, pod, backup, and maintenance tools.
- `tests/`: Python contract and unit tests.
- `experiments/`: prototypes; verify promotion into the production path before depending on them.
