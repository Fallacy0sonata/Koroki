"""Contract tests for the watch-party anti-yapper gate (stream_watch.py).

Owner pillar: commentary is ADDRESSED SPEECH — events + viewers only, never
ambient self-talk. The gate is what enforces that mechanically.
"""

from stream_watch import WatchConfig, WatchSession, evaluate_gate, _jaccard


def _cfg(**over):
    defaults = dict(window_title="test", cooldown_seconds=25.0, novelty_threshold=0.5)
    defaults.update(over)
    return WatchConfig(**defaults)


def test_first_look_is_an_event():
    speak, reason = evaluate_gate(None, "a factory with conveyor belts", 0.0, 1000.0, _cfg())
    assert speak is True
    assert reason == "first_look"


def test_cooldown_blocks_even_big_changes():
    # spoke 5s ago; scene totally different — still silence (anti-yapper)
    speak, reason = evaluate_gate(
        "a quiet menu screen", "an explosion engulfs the entire base",
        last_spoke_at=995.0, now=1000.0, cfg=_cfg(cooldown_seconds=25.0),
    )
    assert speak is False
    assert reason == "cooldown"


def test_static_scene_stays_silent():
    desc = "a tycoon game showing a shop interior with three customers"
    speak, reason = evaluate_gate(desc, desc, last_spoke_at=0.0, now=1000.0, cfg=_cfg())
    assert speak is False
    assert reason.startswith("nothing_new")


def test_scene_change_after_cooldown_is_an_event():
    speak, reason = evaluate_gate(
        "an empty shop interior with shelves",
        "a crowd of new visitors floods through the entrance doors",
        last_spoke_at=0.0, now=1000.0, cfg=_cfg(),
    )
    assert speak is True
    assert reason.startswith("scene_changed")


def test_minor_wording_drift_is_not_an_event():
    # VLM rephrasing the same scene shouldn't trigger speech
    speak, reason = evaluate_gate(
        "a shop interior with three customers browsing the shelves near the counter",
        "a shop interior with three customers browsing shelves by the counter",
        last_spoke_at=0.0, now=1000.0, cfg=_cfg(),
    )
    assert speak is False


def test_jaccard_bounds():
    assert _jaccard("a b c", "a b c") == 1.0
    assert _jaccard("a b", "c d") == 0.0
    assert _jaccard("", "anything") == 0.0


def test_session_tracks_gate_state():
    async def _noop(summary, reason):
        pass

    s = WatchSession(_cfg(), _noop)
    assert s.running is False
    assert s.stats.ticks == 0 and s.stats.events == 0


# ── GM2 step 3: session world-model + idle awareness (2026-07-08) ────


def test_world_model_idle_detection():
    from stream_watch import WatchState
    import time as _t

    st = WatchState(game="Sol's RNG")
    st.last_change_ts = _t.time() - 60  # a minute of nothing
    assert st.idle_seconds() >= 59
    block = st.context_block()
    assert "AFK or idle" in block or "NOTHING" in block
    # activity resets the clock and the warning
    st.note_activity()
    assert st.idle_seconds() < 2
    assert "AFK" not in st.context_block()


def test_world_model_session_age_and_events():
    from stream_watch import WatchState
    import time as _t

    st = WatchState()
    st.session_started_ts = _t.time() - 5 * 60  # 5 minutes in
    st.note_event("rolled a rare aura")
    block = st.context_block()
    assert "watching for ~5 min" in block
    assert "rolled a rare aura" in block


def test_idle_event_fires_once_per_stretch():
    import asyncio
    import time as _t

    fired = []

    async def _on_event(summary, reason):
        fired.append((summary, reason))

    s = WatchSession(_cfg(idle_after_seconds=1.0, cooldown_seconds=0.0), _on_event)
    s.state.last_change_ts = _t.time() - 30
    # simulate two consecutive static ticks (the gate body inline)
    async def _two_static_ticks():
        for _ in range(2):
            now = _t.time()
            if (
                not s._idle_reported
                and s.state.idle_seconds() >= s.cfg.idle_after_seconds
                and now - s._last_spoke_at >= s.cfg.cooldown_seconds
            ):
                s._idle_reported = True
                s._last_spoke_at = now
                await s.on_event("idle note", "idle")

    asyncio.run(_two_static_ticks())
    assert len(fired) == 1 and fired[0][1] == "idle"


# ── blackboard (LIMBS wave 1): exact-text layer + timestamped snapshot ──


def test_blackboard_ocr_layer_compacts_and_overwrites():
    from stream_watch import WatchState

    st = WatchState(game="Sol's RNG")
    st.note_ocr([
        {"text": "Roll"}, {"text": "Coins: 1,204"}, {"text": "Roll"},  # dupe
        {"text": "x"},                                                  # 1-char noise
        {"text": " Shop "},
    ])
    assert st.ocr_text == "Roll | Coins: 1,204 | Shop"
    st.note_ocr([{"text": "Inventory"}])
    assert st.ocr_text == "Inventory"  # overwrite, never append
    # a failed OCR pass keeps the previous text (age tells the rest)
    st.note_ocr(None)
    assert st.ocr_text == "Inventory"


def test_blackboard_snapshot_carries_ages():
    from stream_watch import WatchState

    st = WatchState(game="Sol's RNG")
    st.note_scene("a desert with an aura roll button")
    st.note_ocr([{"text": "Roll"}])
    st.scene_ts -= 12.0  # pretend the look was 12s ago
    snap = st.snapshot()
    assert snap["game"] == "Sol's RNG"
    assert snap["scene"].startswith("a desert")
    assert 11.5 <= snap["scene_age_s"] <= 13.5
    assert snap["ocr"] == "Roll" and snap["ocr_age_s"] < 2
    assert "idle_s" in snap and "session_min" in snap
    # never-observed layers read as None, not 0 (unknown != fresh)
    fresh = WatchState()
    s2 = fresh.snapshot()
    assert s2["scene_age_s"] is None and s2["ocr_age_s"] is None


def test_blackboard_stale_scene_warns_in_context():
    import time as _t

    from stream_watch import WatchState

    st = WatchState()
    st.note_scene("the shop menu is open")
    st.scene_ts = _t.time() - 45
    block = st.context_block()
    assert "last clear look was" in block and "may have moved on" in block
    # a fresh look clears the warning
    st.note_scene("the shop menu is open")
    assert "last clear look" not in st.context_block()


def test_decide_request_accepts_ocr_block():
    from services.orchestrator.routes.games import GameDecideRequest

    req = GameDecideRequest(game="sols rng", scene="a desert",
                            ocr_block="Roll | Coins: 1,204 | Shop")
    assert req.ocr_block.startswith("Roll")
    # bounded: the blackboard caps at 360, the contract at 400
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        GameDecideRequest(game="g", scene="s", ocr_block="x" * 401)
