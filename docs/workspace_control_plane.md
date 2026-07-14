# Koroki Workspace Control Plane

This document records the intended development system around Koroki. It is deliberately smaller
than a maximal plugin stack: each layer must own a distinct job and earn its maintenance cost.

## The layers

| Need | Source of truth | Adopt now | Candidate later |
|---|---|---|---|
| Owner intent and agent rules | `AGENTS.md` + private `CLAUDE.md` | Repository files + repo-local Koroki skill | Refine after real task failures |
| Current work | Private `docs/master_queue.md` | Keep | GitHub Issues after owner authentication |
| Historical decisions | Private `LEGACY.md` | Keep | Search/index only; never duplicate it |
| Code understanding | Code, tests, `rg` | Serena MCP for Python/TypeScript | Remove if three real tasks show no benefit |
| Library documentation | Official primary docs | Web/docs lookup | Context7 only if lookup friction proves real |
| Dependencies | `pyproject.toml`, package locks | `uv` + Ruff installed | Controlled `uv` lock/sync migration |
| Data/model lineage | Current Drive vault + manifests | DVC initialized; local Drive remote configured | Select one artifact after disk cleanup |
| Remote collaboration | Local Git | Private baseline committed; GitHub MCP configured read-only | Owner GitHub login, private push, minimal CI |
| Quality gate | `scripts/check_all.py` | Tests + staged Gitleaks scan | Minimal GitHub Actions after private push |
| Large feature design | Focused design note | Spec Kit installed, use only when justified | Pod/creator arc pilot |

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

## Creator-income stack

Koroki should not be forced to earn only by becoming a content mill. The strongest portfolio is a
stack in which public content is distribution, not the sole product:

1. **Sanitized developer products:** package one narrow, general tool learned from Koroki, such as
   a private AI-workspace safety kit, local-agent recovery/evaluation harness, or approval-gated
   creator workflow. Never ship Koroki's identity, memory, voice/model weights, live integration,
   private history, or exact production configuration.
2. **Licensed software:** if a small product proves useful, sell a maintained desktop utility or
   subscription with license keys. This is semi-passive, because support and updates remain real
   work, but it can sell without Koroki continuously performing.
3. **Membership and one-time releases:** devlogs, art/lore packs, experiments, and polished builds
   can become recurring or one-time fan products after a small audience exists. Minecraft footage
   itself remains freely viewable where the current Usage Guidelines require it.
4. **Productized setup or licensing:** a fixed-scope installation/reliability audit for another
   creator or game-agent project is the quickest plausible cash route, but it is active service
   revenue rather than passive income. Use it to discover which repeated work deserves a product.
5. **A distinct interactive experience:** the stage/world/avatar work can eventually become a
   small original game or companion experience that does not rely on Minecraft assets or branding.
6. **Sanitized open-source sponsorship:** publish only a genuinely reusable, identity-free subset;
   GitHub Sponsors becomes relevant after people actually depend on it, not before.

Minecraft server monetization is possible under specific current rules, but it adds payment
history, pricing disclosure, privacy, fairness, and all-ages obligations. Selling Koroki as an
exclusive advantage inside a server is therefore not the recommended first product. Mass AI
uploads, paid emotional dependency, raw persona/voice/model sales, crypto/NFT mechanics, and
unattended account automation are outside the plan.

Content can still be an approval-gated acquisition loop: record authentic sessions, mark candidate
moments, cut and caption locally, keep provenance/disclosure metadata, and require owner approval
before private/unlisted drafts or publication.

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
- Lemon Squeezy digital products and licensing: <https://docs.lemonsqueezy.com/help/products>
- Patreon one-time purchases: <https://support.patreon.com/hc/en-us/articles/16303719836813-Selling-one-time-purchases-on-Patreon>
- itch.io creator payments: <https://itch.io/docs/creators/payments>
- GitHub Sponsors: <https://docs.github.com/en/sponsors/getting-started-with-github-sponsors/about-github-sponsors>
- Minecraft Usage Guidelines: <https://www.minecraft.net/en-us/usage-guidelines>
- OBS WebSocket: <https://github.com/obsproject/obs-websocket>
- YouTube channel monetization policies: <https://support.google.com/youtube/answer/1311392>
- YouTube Data API uploads: <https://developers.google.com/youtube/v3/guides/uploading_a_video>
