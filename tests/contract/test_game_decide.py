"""Contract tests for the play-cycle decision parser (S3) and PlaySession flow."""

import asyncio

from services.orchestrator.routes.games import parse_decision


# ── parse_decision: the captain's output → executor action ──────────


def test_clean_block_parses():
    raw = (
        "STATE: progressing\n"
        "DO: click the store button\n"
        "SAY: let's see what this shop has"
    )
    d = parse_decision(raw)
    assert d["task_state"] == "progressing"
    assert d["action"] == {"type": "click", "target": "the store button"}
    assert d["say"] == "let's see what this shop has"


def test_silent_say_is_empty():
    d = parse_decision("STATE: blocked\nDO: wait 3\nSAY: [silent]")
    assert d["say"] == ""
    assert d["action"] == {"type": "wait", "seconds": 3.0}
    assert d["task_state"] == "blocked"


def test_press_takes_single_key():
    d = parse_decision("STATE: progressing\nDO: press e to interact\nSAY: [silent]")
    assert d["action"] == {"type": "press", "key": "e"}


def test_scroll_direction():
    assert parse_decision("DO: scroll down")["action"] == {"type": "scroll", "amount": -3}
    assert parse_decision("DO: scroll up")["action"] == {"type": "scroll", "amount": 3}


def test_hold_movement_primitive():
    d = parse_decision("STATE: progressing\nDO: hold w 3\nSAY: heading to the mailbox")
    assert d["action"] == {"type": "hold", "key": "w", "seconds": 3.0}
    # missing duration defaults sanely
    assert parse_decision("DO: hold w")["action"] == {"type": "hold", "key": "w", "seconds": 1.5}


def test_look_and_garbage_degrade_to_look():
    assert parse_decision("DO: look")["action"] == {"type": "look"}
    assert parse_decision("i dunno, maybe the door?")["action"] == {"type": "look"}
    assert parse_decision("")["action"] == {"type": "look"}


def test_case_and_prose_tolerance():
    raw = "state: Regressed\ndo: Click the red exit button.\nsay: oops. wrong menu"
    d = parse_decision(raw)
    assert d["task_state"] == "regressed"
    assert d["action"]["type"] == "click"
    assert "exit button" in d["action"]["target"]
    assert d["say"].startswith("oops")


def test_click_without_target_degrades_to_look():
    assert parse_decision("DO: click")["action"] == {"type": "look"}


# ── PlaySession: forced look on static screens ───────────────────────


def test_forced_look_after_static_streak(monkeypatch, tmp_path):
    import game_agent
    import game_goals

    # Keep test sessions out of the REAL trajectory/skill stores.
    monkeypatch.setattr(game_agent, "TRAJ_DIR", tmp_path)
    monkeypatch.setattr(game_goals, "SKILLS_DIR", tmp_path)

    events = {"describes": 0, "decides": 0}

    cfg = game_agent.PlayConfig(window_title="t", game="g", force_look_every=3)
    session = game_agent.PlaySession(cfg, on_say=lambda s: asyncio.sleep(0))

    monkeypatch.setattr(session, "_resolve_window", lambda: 1234)
    monkeypatch.setattr(game_agent, "capture_window_png", lambda hwnd: b"png")
    monkeypatch.setattr(session._gate, "changed", lambda png: (False, 0.001))

    async def fake_describe(png):
        events["describes"] += 1
        return "a static menu"

    async def fake_decide(scene, mode="play"):
        if mode == "curriculum":  # Game Mind goal review (2026-07-05) — not a play decide
            return {"goal_action": "keep", "goal": ""}
        events["decides"] += 1
        return {"task_state": "progressing", "action": {"type": "look"}, "say": ""}

    monkeypatch.setattr(session, "_describe", fake_describe)
    monkeypatch.setattr(session, "_decide", fake_decide)

    async def run():
        for _ in range(6):
            await session._cycle()

    asyncio.run(run())
    # ticks 1,2 gated; tick 3 forced look; 4,5 gated; 6 forced look
    assert events["describes"] == 2
    assert session.stats.gated == 4
    assert session.stats.looks == 2


def test_parse_hold_click_with_seconds():
    d = parse_decision("STATE: progressing\nDO: hold_click the pump handle 3\nSAY: [silent]")
    assert d["action"] == {"type": "hold_click", "target": "the pump handle", "seconds": 3.0}


def test_parse_hold_click_default_seconds():
    d = parse_decision("DO: hold_click the charge button")
    assert d["action"]["type"] == "hold_click"
    assert d["action"]["target"] == "the charge button"
    assert d["action"]["seconds"] == 1.5


def test_parse_hold_still_works_after_hold_click_added():
    d = parse_decision("DO: hold w 2")
    assert d["action"] == {"type": "hold", "key": "w", "seconds": 2.0}
