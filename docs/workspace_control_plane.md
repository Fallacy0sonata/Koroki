# Koroki Workspace Control Plane

This document records the intended development system around Koroki. It is deliberately smaller
than a maximal plugin stack: each layer must own a distinct job and earn its maintenance cost.

## The layers

| Need | Source of truth | Adopt now | Candidate later |
|---|---|---|---|
| Owner intent and agent rules | `AGENTS.md` + private `CLAUDE.md` | Repository files | Repo-local Koroki skill |
| Current work | Private `docs/master_queue.md` | Keep until Git baseline is clean | Backlog.md or GitHub Issues |
| Historical decisions | Private `LEGACY.md` | Keep | Search/index only; never duplicate it |
| Code understanding | Code, tests, `rg` | Built-in tools | Serena MCP trial |
| Library documentation | Official primary docs | Web/docs lookup | Context7 only if lookup friction proves real |
| Dependencies | `pyproject.toml`, package locks | Existing files | `uv` lock/sync migration |
| Data/model lineage | Current Drive vault + manifests | Existing verified backup | DVC pilot on one dataset/model family |
| Remote collaboration | Local Git | Clean private baseline first | GitHub MCP, read-only and minimal toolsets |
| Quality gate | `scripts/check_all.py` | Yes | Ruff + secret scan + GitHub Actions |
| Large feature design | Focused design note | As needed | Spec Kit only for ambiguous multi-system arcs |

## Admission rule for tools

A tool is admitted only if it removes a measured recurring cost, has an official or auditable
source, works on Windows, can be pinned/removed, does not duplicate an existing owner, and does not
silently gain write access to accounts or live systems. Trial one candidate at a time and record:

- task and baseline time/error rate;
- configuration and permissions granted;
- benefit after three real tasks;
- failure modes, disk/RAM/background-process cost, and uninstall path;
- keep/remove decision.

## Recommended adoption sequence

1. Commit a reviewed private source baseline. Until then, worktrees and CI cannot reproduce the
   modern Koroki because much of it is untracked.
2. Use `AGENTS.md`, `.worktreeinclude`, and the combined local test gate.
3. Add fast local lint and secret detection without mass-formatting the legacy tree.
4. Connect the existing private GitHub remote; enable dependency alerts and a minimal CI gate.
5. Trial GitHub MCP in read-only mode with only context/repository/issue/PR/action toolsets.
6. Trial Serena on cross-file Minecraft or orchestrator work and keep it only if it outperforms
   built-in search on real changes.
7. Migrate Python dependency resolution to `uv`; keep specialized GPU environments separate where
   their requirements conflict.
8. Pilot DVC on one regenerable but expensive artifact family using a dedicated Drive folder.
9. Use Spec Kit for a full-potential pod route or creator pipeline, not for everyday bug fixes.

## Private core and future public surface

The canonical repository contains persona logic, private history, exact recipes, data paths, and
live integrations, so it stays private. A future portfolio or product export is generated from an
allow-list into a separate repository or build directory. Public artifacts may include sanitized
architecture, generalized safety patterns, test/evaluation harnesses, and demonstrations; they do
not include memory, identity corpus, exact adapters, credentials, private docs, or raw datasets.

## Creator-income path

The first realistic income loop is not unattended AI spam. It is an approval-gated creator system:

1. Koroki plays/lives normally and OBS records an authenticated local session.
2. Events and telemetry mark candidate moments while the owner retains the original footage.
3. A local pipeline cuts clips, captions them, proposes titles/descriptions/thumbnails, and packages
   provenance plus disclosure metadata.
4. The owner approves; the uploader sends private/unlisted drafts first.
5. Performance data informs future selection without changing Koroki's character or gameplay goals.

This preserves original authorship and keeps automation in production assistance, where platform
policies are much friendlier than repetitive mass-generated uploads. Memberships and occasional
digital products can follow once an audience exists; they are downstream of consistent authentic
content, not a substitute for it.

## Sources used for the shortlist

- Codex customization: <https://learn.chatgpt.com/docs/customization/overview.md>
- Codex worktrees: <https://learn.chatgpt.com/docs/environments/git-worktrees.md>
- GitHub MCP server: <https://github.com/github/github-mcp-server>
- Serena: <https://github.com/oraios/serena>
- GitHub Spec Kit: <https://github.github.com/spec-kit/>
- Backlog.md: <https://github.com/MrLesk/Backlog.md>
- Beads: <https://github.com/steveyegge/beads>
- uv: <https://docs.astral.sh/uv/>
- Ruff: <https://docs.astral.sh/ruff/>
- DVC Google Drive remote: <https://dvc.org/doc/user-guide/data-management/remote-storage/google-drive>
- OBS WebSocket: <https://github.com/obsproject/obs-websocket>
- YouTube channel monetization policies: <https://support.google.com/youtube/answer/1311392>
- YouTube Data API uploads: <https://developers.google.com/youtube/v3/guides/uploading_a_video>
