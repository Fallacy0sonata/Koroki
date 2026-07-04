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
