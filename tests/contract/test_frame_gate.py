"""Contract tests for the S-tier gaming-eyes pieces (stream_watch.py).

FrameGate (S2): the VLM only looks when pixels structurally changed.
WatchState (S1): overwrite semantics — current truth replaced, events bounded.
"""

import io

import numpy as np
from PIL import Image

from stream_watch import FrameGate, WatchState


def _png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def _frame(base: int = 40, w: int = 640, h: int = 360) -> np.ndarray:
    return np.full((h, w, 3), base, dtype=np.uint8)


# ── S2: the gate ─────────────────────────────────────────────────────


def test_first_frame_always_looks():
    gate = FrameGate()
    changed, ratio = gate.changed(_png(_frame()))
    assert changed is True and ratio == 1.0


def test_static_scene_is_gated():
    gate = FrameGate()
    gate.changed(_png(_frame()))
    changed, ratio = gate.changed(_png(_frame()))
    assert changed is False
    assert ratio < 0.01


def test_pixel_noise_is_gated():
    rng = np.random.default_rng(7)
    gate = FrameGate()
    base = _frame()
    gate.changed(_png(base))
    noisy = base + rng.integers(-6, 7, base.shape).astype(np.int16)
    changed, _ = gate.changed(_png(np.clip(noisy, 0, 255)))
    assert changed is False  # sub-threshold speckle must not wake the VLM


def test_structural_change_triggers():
    gate = FrameGate()
    base = _frame()
    gate.changed(_png(base))
    event = base.copy()
    event[100:250, 200:450] = 230  # a big bright panel appears (menu/enemy/dialog)
    changed, ratio = gate.changed(_png(event))
    assert changed is True
    assert ratio > 0.02


def test_slow_drift_absorbed_by_ema():
    """A brightness creep of +2/frame (slow pan/day-night cycle) never triggers."""
    gate = FrameGate()
    level = 40
    gate.changed(_png(_frame(level)))
    fired = []
    for _ in range(12):
        level += 2
        changed, _ = gate.changed(_png(_frame(level)))
        fired.append(changed)
    assert not any(fired)


def test_abrupt_change_after_drift_still_triggers():
    gate = FrameGate()
    level = 40
    gate.changed(_png(_frame(level)))
    for _ in range(6):
        level += 2
        gate.changed(_png(_frame(level)))
    jump = _frame(level)
    jump[:, :] = 220  # scene cut
    changed, _ = gate.changed(_png(jump))
    assert changed is True


# ── S1: the rolling state ────────────────────────────────────────────


def test_scene_is_overwritten_not_appended():
    s = WatchState(game="tycoon")
    s.note_scene("an empty shop")
    s.note_scene("a crowded shop")
    assert s.current_scene == "a crowded shop"
    assert "empty" not in s.current_scene  # stale truth is GONE


def test_events_are_bounded():
    s = WatchState()
    for i in range(10):
        s.note_event(f"event number {i}")
    assert len(s.recent_events) == 4
    block = s.context_block()
    assert "event number 9" in block
    assert "event number 0" not in block  # old history dropped, not accumulated


def test_empty_state_has_no_context():
    assert WatchState().context_block() == ""
