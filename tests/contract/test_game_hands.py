"""Contract tests for her hands (game_hands.py) — no real input, no GPU.

The rails are the contract: dry-run never touches pydirectinput, panic freezes
everything, coordinates never escape the window rect.
"""

import asyncio

import pytest

from game_hands import ACTION_TYPES, PANIC_FILE, GameHands


def _hands(**over):
    h = GameHands(window_title="TestGame", dry_run=True, min_action_gap_s=0.0)
    for k, v in over.items():
        setattr(h, k, v)
    return h


# ── pure coordinate math ─────────────────────────────────────────────


def test_norm_to_screen_scales_into_window():
    rect = (100, 200, 1100, 800)  # 1000x600 window at (100,200)
    assert GameHands.norm_to_screen(0.0, 0.0, rect) == (100, 200)
    assert GameHands.norm_to_screen(1.0, 1.0, rect) == (1100, 800)
    assert GameHands.norm_to_screen(0.5, 0.5, rect) == (600, 500)


def test_clamp_confines_to_rect():
    rect = (100, 200, 1100, 800)
    assert GameHands.clamp_to_rect(0, 0, rect) == (104, 204)
    assert GameHands.clamp_to_rect(5000, 5000, rect) == (1096, 796)
    assert GameHands.clamp_to_rect(600, 500, rect) == (600, 500)


# ── action vocabulary ────────────────────────────────────────────────


def test_unknown_action_rejected():
    h = _hands()
    result = asyncio.run(h.act({"type": "format_disk"}))
    assert result["ok"] is False
    assert "unknown" in result["detail"]


def test_vocabulary_is_closed():
    # Growing this set is a CONSCIOUS act — update here only alongside real
    # executor support + parser support + rails review.
    assert ACTION_TYPES == {
        "click", "double_click", "right_click", "move_to",
        "press", "hold", "scroll", "wait",
    }


def test_click_requires_target():
    h = _hands()
    result = asyncio.run(h.act({"type": "click"}))
    assert result["ok"] is False
    assert "target" in result["detail"]


def test_wait_is_bounded():
    h = _hands()
    result = asyncio.run(h.act({"type": "wait", "seconds": 9999}))
    assert result["ok"] is True
    assert "10.0s" in result["detail"]


# ── rails ────────────────────────────────────────────────────────────


def test_panic_file_freezes_hands(tmp_path, monkeypatch):
    import game_hands as gh

    panic = tmp_path / "PANIC"
    panic.write_text("stop")
    monkeypatch.setattr(gh, "PANIC_FILE", panic)
    h = _hands()
    result = asyncio.run(h.act({"type": "wait", "seconds": 0.1}))
    assert result["ok"] is False
    assert "panic" in result["detail"]
    assert h.stats.refused == 1


def test_dry_run_never_imports_input_library(monkeypatch):
    # If dry-run ever touches pydirectinput, this import hook screams.
    import builtins

    real_import = builtins.__import__

    def guarded(name, *a, **k):
        if name == "pydirectinput":
            raise AssertionError("dry-run must not import pydirectinput")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", guarded)
    h = _hands()
    # press with no window → refused BEFORE any input library involvement
    result = asyncio.run(h.act({"type": "press", "key": "e"}))
    assert result["ok"] is False


def test_refuses_press_without_foreground_window(monkeypatch):
    h = _hands()
    monkeypatch.setattr(h, "_window_rect", lambda: None)
    result = asyncio.run(h.act({"type": "press", "key": "space"}))
    assert result["ok"] is False
    assert "window" in result["detail"]
    assert h.stats.refused == 1


def test_dry_run_click_with_mocked_target(monkeypatch):
    h = _hands()

    async def fake_resolve(target):
        return (640, 480)

    monkeypatch.setattr(h, "resolve_target", fake_resolve)
    result = asyncio.run(h.act({"type": "click", "target": "the buy button"}))
    assert result["ok"] is True
    assert result.get("dry_run") is True
    assert "(640,480)" in result["detail"]
    assert h.stats.dry_runs == 1
    assert h.stats.actions == 0  # dry runs never count as real actions


def test_point_miss_is_refusal(monkeypatch):
    h = _hands()

    async def fake_resolve(target):
        return None

    monkeypatch.setattr(h, "resolve_target", fake_resolve)
    result = asyncio.run(h.act({"type": "click", "target": "a nonexistent thing"}))
    assert result["ok"] is False
    assert h.stats.refused == 1
