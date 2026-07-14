"""IDM action space + frame config — the one place the contract lives.

The Inverse Dynamics Model (LIMBS Stage 1, docs/game_limbs_verdict_2026-07-09.md
addendum) learns video -> action from the owner's LABELED recorder sessions,
then pseudo-labels unlabeled YouTube (VPT, arXiv 2206.11795). This module fixes
the action vocabulary both the recorder-session reader and the model heads agree
on. Changing it = retrain; keep it stable.

Design choices:
- NON-CAUSAL window (past + future frames around the target). VPT's key trick:
  seeing what happens AFTER an input makes "what caused this" far easier than a
  causal policy could ever be. The IDM is a labeler, not a player — it may cheat
  with the future.
- Roblox-family action space: WASD+jump+sprint movement, a small interact set,
  two mouse buttons, and continuous camera (the raw WM_INPUT deltas the recorder
  captures through pointer lock). Camera is the signal a cursor-position log
  would lose entirely.
"""

from __future__ import annotations

# frame stack: FRAMES_BEFORE + 1 (target) + FRAMES_AFTER, all fed together
FRAMES_BEFORE = 2
FRAMES_AFTER = 1
STACK = FRAMES_BEFORE + 1 + FRAMES_AFTER  # 4

FRAME_SIZE = 128          # square grayscale analysis resolution
FRAME_CHANNELS = 1        # grayscale — UI/motion cues survive, 4x less compute

# Keys the IDM predicts (multi-label: any subset can be held). Order is the
# vector layout — append only, never reorder.
KEY_VOCAB = ("w", "a", "s", "d", "space", "shift",
             "e", "q", "f", "r", "1", "2", "3", "4")
N_KEYS = len(KEY_VOCAB)

# Mouse buttons held (multi-label).
BUTTON_VOCAB = ("left", "right")
N_BUTTONS = len(BUTTON_VOCAB)

# Camera = 2 continuous values (dx, dy), normalized by this px scale into
# roughly [-1, 1] so the regression head and the classification heads share a
# loss magnitude. A full fast flick is ~this many px in one frame interval.
CAMERA_SCALE = 300.0

KEY_INDEX = {k: i for i, k in enumerate(KEY_VOCAB)}
BUTTON_INDEX = {b: i for i, b in enumerate(BUTTON_VOCAB)}
