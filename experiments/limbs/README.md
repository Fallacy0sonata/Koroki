# experiments/limbs

The game-limbs research + data pipeline (LIMBS arc). Verdict & staged plan:
`docs/game_limbs_verdict_2026-07-09.md`. The plan is VPT-style: owner's labeled
play + IDM → pseudo-label unlabeled YouTube → behavior-clone a small per-game
"limbs" policy that the captain rides on top of.

## The pieces (data flows top to bottom)

| File / dir | Role | Status |
|---|---|---|
| `../../demo_recorder.py` | **Stage 0.** Owner plays → focus-gated video + input log (WM_INPUT raw camera deltas). Sessions → G: limbs_demos. | shipped |
| `../../scripts/harvest_gameplay.py` | Banks external gameplay VODs (yt-dlp) → G: limbs_youtube. Duration-ranked, montage-blacklisted, relevance-gated, **verdict-at-ingest**. | shipped |
| `footage_filter.py` | Segment-level quality gate: cut detection (2-stage histogram for camera whips), static-drop, usable_ratio → `quality.jsonl`. The hard gate before any training. | shipped |
| `session_dataset.py` | Recorder session → aligned per-frame action bins. Validated lossless. | shipped |
| `corpus_profiler.py` | Profiles usable corpus per game → vision-only **tractability ranking** (which game to debut on) + real UI vocabulary. | shipped |
| `idm/` | **Stage 1.** Inverse-dynamics model (video→action labeler). Scaffold built + synthetic-validated; push-button for the 3090. | scaffold |

## What's proven vs what waits for the 3090 / owner hours
- **Proven now (no real data needed):** the recorder captures losslessly; the
  filter separates gameplay from montage (bimodal, clean gap); the session
  reader aligns frames to actions losslessly; the IDM machine learns all three
  action heads on synthetic. Every link validated before real hours bank.
- **Waits on owner hours:** the real IDM training run (needs banked sessions).
- **Waits on the 3090:** IDM at scale, YouTube pseudo-labeling (Stage 2), the
  behavior-clone policy (Stage 3).

## Corpus state (2026-07-10)
~22.4 usable hours across 7 Roblox games (sols_rng, blox_fruits, pet_simulator_99,
tower_of_hell, theme_park_tycoon_2, doors_roblox, grow_a_garden), all
verdict-tagged in per-game `quality.jsonl`. VODs are the good source — uncut by
construction. Popular edited "progression movies" are ~92% of regular uploads
and all rejected. Owner recordings are the gold tier (real labels, zero cuts).

## Roblox-family policy
v1 limbs = Roblox family only (one control scheme, genre-diverse, 99% free),
weighted toward the debut game. Cross-engine games = dilution at small-model
scale (even Lumine's zero-shot was within-family) → a later adapter/family, not
v1's training mix.
