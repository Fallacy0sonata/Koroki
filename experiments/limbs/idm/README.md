# IDM — Inverse Dynamics Model (LIMBS Stage 1)

The bottleneck stage of the VPT pipeline (docs/game_limbs_verdict_2026-07-09.md
addendum). Learns **video → action** from the owner's LABELED recorder sessions,
then pseudo-labels unlabeled YouTube gameplay so the final behavior-clone has a
100× larger corpus than the owner could ever record by hand.

Why this exists as a scaffold *now*, before the data does: it's the critical
path, and building + proving it early means the 3090 run is push-button the day
enough hours are banked. Same discipline that validated the recorder and
session_dataset losslessly before real hours existed.

## Files
| File | What |
|---|---|
| `config.py` | Action space + frame contract. The one place recorder-reader and model heads agree. Append-only. |
| `model.py` | Frame-stack CNN (~4.8M params), 3 heads: keys (multi-label), buttons (multi-label), camera (2 regressed deltas). |
| `synthetic.py` | Synthetic gameplay that encodes a known action into a frame stack — proves the machine LEARNS without real data. |
| `data.py` | Real recorder-session → (frame_stack, action) pairs. Reuses session_dataset.bin_events. `encode_action` is pure + tested. |
| `train.py` | Training loop; `--synthetic` (proof) or `--sessions <dir>` (real). Checkpoints to G: Koroki Storage. |
| `test_idm.py` | Pure-piece unit tests (action encoding, shapes, loss) — no GPU/video. |

## Design decisions
- **Non-causal window** (2 past + target + 1 future frame). VPT's key trick: an
  IDM is a *labeler*, not a player, so it may see the future — "what happened
  after this input" makes "what was the input" far easier than any causal policy.
- **Small on purpose** (~4.8M params). Per-game labeler that must train in ~an
  hour on a 3090, not a foundation model.
- **Roblox-family action space** (config.py): WASD+jump+sprint, small interact
  set, 2 mouse buttons, continuous camera. Camera = the raw WM_INPUT deltas the
  recorder captures through pointer lock — the signal a cursor log would lose.

## Validation (done 2026-07-10/11)
Synthetic run, 2000 steps, GPU, ~12s:
```
key_f1 1.000 · btn_acc 1.000 · cam_mae 0.049 (prior 0.50)
```
All three heads learn their target from the frame stack → the training loop,
losses, and head plumbing are proven correct.

**End-to-end pipeline proven (2026-07-11):** synthetic recorder session →
`precache_session` → `CachedCorpus` → `train_sessions` ran to completion and
LEARNED on the cached data (key_f1 0→0.9, cam_mae→0.03 in 60 CPU steps). The
whole Stage-1 flow (recorder format → training) is push-button; only real banked
hours are missing. Two synthetic traps burned through
to get there: a pure-noise background makes camera unrecoverable (no trackable
structure); periodic gratings alias (a one-period shift looks identical). The
signal must be non-periodic — Gaussian-smoothed noise.

## Run
```powershell
# prove the machine learns (CPU ok, ~1 min; GPU ~12s):
.venv\Scripts\python.exe -m experiments.limbs.idm.train --synthetic --steps 2000
# real run once sessions are banked:
.venv\Scripts\python.exe -m experiments.limbs.idm.train --sessions data/demo_recordings --steps 20000
# unit tests:
.venv\Scripts\python.exe -m pytest experiments/limbs/idm/test_idm.py -q
```

## Before the real 3090 run (TODOs, not blockers now)
- ~~Pre-decode frames to a cache.~~ **DONE 2026-07-11** — `precache.py`:
  `precache_session` decodes each session once to a compressed `.npz` (uint8
  grayscale frames + aligned targets; a stack is a slice, no 4x dup);
  `CachedCorpus` trains from array slices, no video seeking. `train.py
  --sessions <dir>` auto-detects `.npz`. Validated LOSSLESS vs the direct reader
  (action targets exact, frames byte-identical: max diff 0.0000).
- **Mouse-button HOLD state.** `encode_action` currently reads button *presses*
  (recorder logs md/mu but session_dataset bins only carry press events); add
  held-button tracking like held-keys for true hold labels.
- **Camera scale calibration.** `CAMERA_SCALE=300` is a guess for "one fast
  flick"; recalibrate from real recorded delta distributions once hours exist.
- **Stage 2**: apply the trained IDM to the filtered YouTube corpus → pseudo-
  labels → merge with owner labels → behavior-clone the small VLM policy.
