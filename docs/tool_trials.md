# Koroki Tool Trial Log

Record evidence here before adding a viral developer tool to the permanent workspace. A star count
is discovery evidence, not trust evidence. The keep decision depends on a scoped local trial,
permissions, overlap, maintenance cost, privacy boundaries, and measured benefit on Koroki.

## 2026-07-14 — Graphify 0.9.15

Source: <https://github.com/Graphify-Labs/graphify>

### Trial boundary

- Ran the pinned PyPI package in an isolated `uvx` environment.
- Used a temporary Git snapshot containing only `clients/minecraft-bot` from the tested commit.
- Used `extract --code-only --no-cluster`; all model/API environment variables were removed from
  the trial process.
- Did not run `graphify install`, install its Codex skill or hooks, start its MCP server, enable
  watch mode, ingest URLs, or process private docs/media.
- Removed the temporary graph after inspection.

### Results

- Indexed 41 files in 2.54 seconds.
- Produced 460 nodes and 1,178 edges in a 0.904 MB graph.
- `affected` correctly connected `actionableProject()` with `maybeConsultDirector()` and
  `decideAndAct()`.
- A natural-language project-recovery query found the relevant project, verification, bot, and
  skills modules, but returned a noisy neighborhood rather than an answer.
- A shortest path between project selection and gather-failure handling was structurally valid but
  only passed through shared file imports; it was not a causal execution path.
- `pip-audit` found no known vulnerabilities in the pinned package environment. The repository is
  MIT licensed and its latest default-branch CI run passed.

### Concerns

- The project is pre-1.0 and releasing rapidly. Open issues at trial time included Python qualified
  calls missing call edges, incremental-update regressions, nested-ignore regressions, stale
  installed skills after upgrade, and same-basename node collisions.
- The default Codex installer edits `AGENTS.md`, installs `.codex/hooks.json`, and changes normal
  codebase questions into mandatory graph-first lookups. That conflicts with Koroki's minimal,
  reality-checked workflow and duplicates Serena.
- Its published code-intelligence result used six questions on one large repository and did not
  compare with Serena or language-server symbol/reference tools. It is evidence of potential, not
  evidence that it improves this repository.
- Code-only AST extraction is local. Semantic processing of documents/media can use the host agent
  or configured external model APIs, so it is inappropriate for Koroki's private identity/history
  corpus without a separate privacy review.
- Graph HTML recently received a stored-XSS fix. Generated graphs must remain local and ignored.

### Verdict

Do not install Graphify's skill, hooks, MCP server, watcher, or semantic pipeline. Keep it as an
optional pinned, manual, code-only experiment for unusually broad cross-language impact questions.
Use `rg` and Serena first. Reconsider after a stable release and after the current correctness
regressions settle.

## Candidate shortlist

| Candidate | Verdict | Why |
|---|---|---|
| [ast-grep](https://github.com/ast-grep/ast-grep) | Trial next when needed | Mature local structural search/rewrite; complements `rg` and Serena without a daemon or account. |
| [OSV-Scanner](https://github.com/google/osv-scanner) | Add after dependency locks | Strong dependency-vulnerability coverage; network mode sends package metadata and hashes, not source, and offline mode exists. |
| [prek](https://github.com/j178/prek) | Defer | Fast hook manager, but hooks add friction before the Python dependency split and focused checks are settled. |
| [Graphite](https://graphite.com/docs/cli-overview) | Defer | Stacked PRs help multi-reviewer teams; the current solo/private workflow does not justify another GitHub-authorized service. |
| [GitNexus](https://github.com/nxpatterns/gitnexus) | Skip for now | Young, no asserted repository license at trial time, and overlaps Serena/Graphify while adding embeddings and MCP configuration. |

## Higher-value native GitHub work

1. Split GPU/model packages from the core Python dependency set, then create a reproducible
   `uv.lock`. The current monolithic Torch/bitsandbytes install blocks a cheap Python CI runner.
2. Add minimal Minecraft CI first; it can use the existing lockfile and does not need live login or
   secrets.
3. Add Python CI after the dependency split, using the same `scripts/check_all.py` entry point.
4. Enable conservative dependency alerts/updates only after lockfiles make the results
   reproducible. Prefer security updates over high-volume automatic version churn.
