"""Synthetic gameplay for validating the IDM end-to-end WITHOUT real data.

The IDM's whole thesis is "held input -> visible on-screen state + world motion,
which a CNN can read back into the input." This generates exactly that, so the
training loop, heads, and losses can be proven to LEARN before a single hour of
real footage is banked (the same discipline that validated session_dataset.py
lossless).

Each sample encodes a random action into a 4-frame stack:
- camera (dx, dy): the textured background SCROLLS by this velocity across the
  stack -> recoverable from inter-frame shift (regression head).
- keys: each held key lights a bar in a fixed HUD slot, present in all frames
  (multi-label head).
- buttons: left/right held -> a bright block in the top-left/right corner.

A model that drives all three losses down has working plumbing for all three
heads. It is NOT a claim about real-footage difficulty — only that the machine
is wired correctly.
"""

from __future__ import annotations

import numpy as np
import torch

from .config import FRAME_SIZE, N_BUTTONS, N_KEYS, STACK

_RNG = np.random.default_rng(7)


def _make_field() -> np.ndarray:
    """A NON-PERIODIC low-frequency field (smoothed noise), not gratings.

    Camera velocity is only recoverable if the scrolling background has
    trackable, UNAMBIGUOUS structure. Pure noise has none (cam stuck at the
    prior); periodic gratings alias (a shift of one period looks identical, cam
    still stuck). Gaussian-smoothed noise gives coherent, non-repeating edges
    the CNN can lock a shift onto — both failure modes learned 2026-07-10."""
    import cv2

    n = FRAME_SIZE * 3
    field = _RNG.random((n, n)).astype("float32")
    field = cv2.GaussianBlur(field, (0, 0), sigmaX=6.0)
    field -= field.min()
    field /= field.max() + 1e-9
    return field.astype("float32")


_FIELD = _make_field()
SYN_SHIFT_PX = 20.0  # a normalized camera value of 1.0 scrolls this many px/frame


def _render(cam_nx: float, cam_ny: float, keys: np.ndarray, buttons: np.ndarray) -> np.ndarray:
    """One action -> a (STACK, H, W) float32 frame stack in [0,1].

    cam_nx/cam_ny are NORMALIZED camera in [-1, 1]; scroll = normalized *
    SYN_SHIFT_PX per frame (kept small enough that the window never clips)."""
    frames = np.zeros((STACK, FRAME_SIZE, FRAME_SIZE), dtype="float32")
    base_x, base_y = FRAME_SIZE, FRAME_SIZE  # center window into the field
    for t in range(STACK):
        ox = int(base_x + cam_nx * SYN_SHIFT_PX * t)
        oy = int(base_y + cam_ny * SYN_SHIFT_PX * t)
        ox = max(0, min(ox, FRAME_SIZE * 2 - 1))
        oy = max(0, min(oy, FRAME_SIZE * 2 - 1))
        frame = _FIELD[oy:oy + FRAME_SIZE, ox:ox + FRAME_SIZE].copy()

        # HUD key bars along the bottom edge. Clear the strip to a dark plate
        # first so a lit slot is UNAMBIGUOUS against the textured field.
        hud_h = 14
        frame[FRAME_SIZE - hud_h:FRAME_SIZE, :] = 0.0
        slot_w = FRAME_SIZE // N_KEYS
        for i in range(N_KEYS):
            if keys[i]:
                x0 = i * slot_w
                frame[FRAME_SIZE - hud_h + 2:FRAME_SIZE - 1, x0 + 1:x0 + slot_w - 1] = 1.0

        # button blocks in the top corners (dark plate + bright fill)
        frame[0:14, 0:14] = 0.0
        frame[0:14, FRAME_SIZE - 14:FRAME_SIZE] = 0.0
        if buttons[0]:  # left
            frame[2:12, 2:12] = 1.0
        if N_BUTTONS > 1 and buttons[1]:  # right
            frame[2:12, FRAME_SIZE - 12:FRAME_SIZE - 2] = 1.0
        frames[t] = frame
    return frames


def sample_batch(batch: int, seed: int | None = None) -> tuple[torch.Tensor, dict]:
    """Random actions -> (x, target dict) tensors on CPU."""
    rng = np.random.default_rng(seed) if seed is not None else _RNG
    xs = np.zeros((batch, STACK, FRAME_SIZE, FRAME_SIZE), dtype="float32")
    keys = (rng.random((batch, N_KEYS)) < 0.25).astype("float32")
    buttons = (rng.random((batch, N_BUTTONS)) < 0.2).astype("float32")
    # normalized camera target in [-1, 1] (uniform -> strong, unbiased gradient)
    cam = rng.uniform(-1.0, 1.0, size=(batch, 2)).astype("float32")
    for b in range(batch):
        xs[b] = _render(cam[b, 0], cam[b, 1], keys[b], buttons[b])
    target = {
        "keys": torch.from_numpy(keys),
        "buttons": torch.from_numpy(buttons),
        "camera": torch.from_numpy(cam),
    }
    return torch.from_numpy(xs), target
