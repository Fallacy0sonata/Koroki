---
name: koroki-workspace
description: Develop, diagnose, review, or plan work in the Koroki repository while preserving live services, private identity data, safety rails, model/runtime constraints, and the owner's uncommitted experiments. Use for Koroki architecture, Python services, Discord, Minecraft player competence, web/avatar surfaces, training/evaluation, pod deployment, backups, workspace automation, portfolio separation, or creator-income tooling.
---

# Koroki Workspace

Work from repository reality without disturbing the running companion or leaking the private core.

## Establish reality

1. Read the nearest `AGENTS.md` completely.
2. Read `README.md`, then private `CLAUDE.md` when present.
3. Read only the newest relevant `LEGACY.md` entries and current-position portion of
   `docs/master_queue.md`.
4. Inspect the implementing code, configuration shape, tests, and `git status` before accepting a
   documentation claim. Prefer code/tests/live config when sources disagree.
5. Identify whether Discord, Minecraft, supervisors, model servers, training, or recording may be
   live. Do not probe by restarting them.

## Set the task boundary

Classify the request before acting:

- **Explain/review/diagnose:** inspect and report; do not mutate live or external state.
- **Build/fix:** edit the smallest coherent production path and its focused tests.
- **Live operation:** require explicit scope for starts, stops, relogs, configuration swaps, input
  control, uploads, publishing, or account writes.
- **Experiment:** keep it isolated until evidence supports promotion; do not create a second
  production implementation.

Record mentally which source paths, runtime surfaces, private files, tests, and restart boundaries
the task touches. Preserve unrelated owner changes in the dirty working tree.

## Investigate efficiently

- Start with `rg` and `rg --files`; follow symbols, contracts, call sites, and nearby tests.
- Check whether the requested capability already exists under another name or subsystem.
- Separate deterministic policy from model judgment. Keep accounting, recovery, permissions,
  confinement, and state ownership in code.
- Treat old phase documents and archived experiments as evidence, not active specifications.
- Browse current primary sources for unstable libraries, services, platform rules, or product
  recommendations. Do not install a fashionable tool without a measured recurring need.

## Preserve Koroki-specific invariants

### Minecraft

Optimize whole-expedition competence rather than verb count. Preserve task continuity, carried vs.
banked vs. lifetime inventory, failure causes, reusable homes/stations/storage, route economy,
desync/death recovery, and postcondition verification. Keep owner-only controls and purchase/focus
guards fail-closed.

### Pod and models

Improve reliable body mechanics and observability before increasing captain capacity. Keep GPU
residency explicit. Treat the routed reasoner/persona path as an evaluated profile with rollback,
not an in-place overwrite of the live configuration.

### Identity and data

Keep persona history, memories, raw conversations, training corpora, exact private recipes, model
weights, credentials, and owner identifiers out of Git and public exports. Build portfolio or
commercial artifacts from an allow-list into a separate sanitized surface.

### Creator and income systems

Keep collection and generation local where practical. Require owner approval before uploads,
publishing, sales, messages, or account changes. Preserve provenance and platform disclosure data.
Never optimize engagement in a way that changes Koroki's identity, safety, or primary play goals.

## Implement and verify

1. Make the smallest production change that owns the behavior.
2. Add or update a focused behavioral test.
3. Run focused tests first.
4. Run the relevant repository gate:

   ```powershell
   .\.venv\Scripts\python.exe scripts\check_all.py --python-only
   .\.venv\Scripts\python.exe scripts\check_all.py --minecraft-only
   .\.venv\Scripts\python.exe scripts\check_all.py
   ```

5. Do not mass-format legacy files. Run lint only on intentionally touched scope until a baseline
   cleanup is approved.
6. State whether a natural service restart is required; do not perform it implicitly.

## Maintain continuity

- Update `LEGACY.md` only for durable lessons, failures, architectural decisions, or milestones.
- Update `docs/master_queue.md` only when current priority/status actually changes.
- Keep public docs sanitized and consistent with demonstrated behavior.
- Before staging or committing, review the exact path list and scan for secrets. Never include
  ignored private context merely because a worktree can access it.
- Hand off with outcome, changed paths, verification evidence, unresolved risks, and owner-only next
  actions.
