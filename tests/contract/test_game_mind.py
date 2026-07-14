"""Game Mind contract tests (agency arc, 2026-07-05).

The pieces that turn 'she sees buttons' into 'she plays': objective stack,
outcome memory, progress metrics, skill library, and the extended decide
grammar (push_goal/pop_goal/skill/save_skill meta-actions).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from game_goals import GENRE_TEMPLATES, GameMind, ObjectiveStack, OutcomeLog, ProgressTracker
from services.orchestrator.routes.games import parse_curriculum, parse_decision


# ── objective stack ──


def test_objective_stack_depth_first() -> None:
    st = ObjectiveStack("finish the game")
    st.push("earn 100 gold")
    st.push("kill the rat blocking the mine")   # distraction interrupts
    assert st.current() == "kill the rat blocking the mine"
    assert st.pop() == "kill the rat blocking the mine"
    assert st.current() == "earn 100 gold"      # original intent survives
    block = st.block()
    assert "FINAL GOAL: finish the game" in block
    assert "-> NOW earn 100 gold" in block


def test_empty_stack_invites_push() -> None:
    st = ObjectiveStack("win")
    assert "push_goal" in st.block()


# ── outcome log ──


def test_outcome_log_pairs_action_with_effect() -> None:
    log = OutcomeLog()
    log.action_taken("click Collect button")
    log.observe_effect(screen_changed=True, metric_deltas=["money: 120 -> 145"])
    assert "click Collect button -> money: 120 -> 145" in log.block()


def test_failure_streak_counts_trailing_no_effects() -> None:
    log = OutcomeLog()
    for _ in range(3):
        log.action_taken("click the locked door")
        log.observe_effect(screen_changed=False, metric_deltas=[])
    assert log.failure_streak() == 3
    log.action_taken("press e")
    log.observe_effect(screen_changed=True, metric_deltas=[])
    assert log.failure_streak() == 0            # success resets


# ── progress tracker ──


def test_progress_tracker_reports_deltas() -> None:
    pt = ProgressTracker(["the money amount"])
    assert pt.record("the money amount", "120") is None       # first sample: no delta
    delta = pt.record("the money amount", "145")
    assert delta and "120 -> 145" in delta
    assert "120 -> 145" in pt.block().replace("  ", " ")


# ── skill library (tmp-pathed via slug isolation) ──


def test_skill_save_and_replay(tmp_path, monkeypatch) -> None:
    import game_goals
    monkeypatch.setattr(game_goals, "SKILLS_DIR", tmp_path)
    mind = GameMind(game="testgame", genre="tycoon")
    mind.note_successful_action({"type": "click", "target": "mailbox"})
    mind.note_successful_action({"type": "click", "target": "collect"})
    assert mind.skills.save("collect_mail", mind.recent_success_steps)
    steps = mind.skills.get("collect_mail")
    assert steps == [{"type": "click", "target": "mailbox"},
                     {"type": "click", "target": "collect"}]
    assert "collect_mail" in mind.skills.block()


def test_genre_template_seeds_final_goal() -> None:
    mind = GameMind(game="paperclips", genre="idle_incremental")
    assert "ending" in mind.goals.final_goal
    assert mind.progress.questions  # idle games watch the core number


# ── decide grammar v2 ──


def test_parse_meta_actions() -> None:
    d = parse_decision("STATE: progressing\nDO: push_goal buy the autoclipper upgrade\nSAY: [silent]")
    assert d["action"] == {"type": "push_goal", "goal": "buy the autoclipper upgrade"}

    d = parse_decision("STATE: progressing\nDO: pop_goal\nSAY: done with that")
    assert d["action"] == {"type": "pop_goal"}

    d = parse_decision("STATE: blocked\nDO: skill collect_mail\nSAY: [silent]")
    assert d["action"] == {"type": "skill", "name": "collect_mail"}

    d = parse_decision("STATE: progressing\nDO: save_skill collect_mail\nSAY: [silent]")
    assert d["action"] == {"type": "save_skill", "name": "collect_mail"}


def test_parse_curriculum_review() -> None:
    assert parse_curriculum("GOAL_ACTION: keep")["goal_action"] == "keep"
    assert parse_curriculum("GOAL_ACTION: pop") == {"goal_action": "pop", "goal": ""}
    r = parse_curriculum("GOAL_ACTION: push make the first 50 paperclips by hand")
    assert r == {"goal_action": "push", "goal": "make the first 50 paperclips by hand"}
    r = parse_curriculum("GOAL_ACTION: pop_then_push buy the first autoclipper")
    assert r == {"goal_action": "pop_then_push", "goal": "buy the first autoclipper"}
    # push without a goal degrades to keep; garbage degrades to keep
    assert parse_curriculum("GOAL_ACTION: push")["goal_action"] == "keep"
    assert parse_curriculum("sure, sounds good!")["goal_action"] == "keep"


def test_real_money_rail_tiers() -> None:
    """Gift Shop incident (2026-07-05) + owner's counterpoint: business games
    legitimately use commerce words. Two tiers: unambiguous payment surfaces
    hard-block; commerce-flavored terms get a vision check; plain gameplay clears."""
    from game_hands import classify_commerce_risk as risk

    # HARD BLOCK — no game needs these clicked
    assert risk("PayPal login") == "block"
    assert risk("enter credit card number") == "block"
    assert risk("redeem code / gift card") == "block"

    # VERIFY — might be gameplay (supermarket checkout!), eyes decide with the frame
    assert risk("Gift Shop") == "verify"
    assert risk("T-Shirts: Gift Shop link") == "verify"
    assert risk("checkout lane 3") == "verify"
    assert risk("the subscribe button") == "verify"
    assert risk("Get it on Google Play") == "verify"
    assert risk("donate button") == "verify"

    # CLEAR — plain in-game commerce never pays the vision tax
    assert risk("Buy Autoclipper ($5.00)") == "clear"
    assert risk("the store button") == "clear"
    assert risk("upgrade shop tab") == "clear"
    assert risk("Make Paperclip") == "clear"


def test_parse_physical_actions_still_work() -> None:
    d = parse_decision("STATE: progressing\nDO: click the Make Paperclip button\nSAY: one more")
    assert d["action"]["type"] == "click"
    d = parse_decision("STATE: progressing\nDO: hold w 3\nSAY: [silent]")
    assert d["action"] == {"type": "hold", "key": "w", "seconds": 3.0}


# ── closed-loop skills (LIMBS wave 1): context precondition + postcondition ──


def test_ocr_keywords_filters_noise() -> None:
    from game_goals import ocr_keywords

    words = ocr_keywords("Roll | Coins: 1,204 | x | Shop | Roll | 999 | Inventory Menu Extra More")
    assert "roll" in words and "shop" in words
    assert "204" not in words and "999" not in words   # numbers change — never preconditions
    assert "x" not in words                            # 1-char UI noise
    assert len(words) <= 6                             # bounded
    assert words.count("roll") == 1                    # deduped


def test_precondition_ok_semantics() -> None:
    from game_goals import precondition_ok

    assert precondition_ok([], "anything") is True           # unknown context: permissive
    assert precondition_ok(None, "anything") is True
    assert precondition_ok(["shop", "buy"], "the SHOP menu") is True
    assert precondition_ok(["shop", "buy"], "a desert with rocks") is False


def test_skill_library_stores_context_words(monkeypatch, tmp_path) -> None:
    import game_goals

    monkeypatch.setattr(game_goals, "SKILLS_DIR", tmp_path)
    lib = game_goals.SkillLibrary("testgame")
    lib.save("open_shop", [{"type": "click", "target": "shop"}],
             context_words=["shop", "coins"])
    assert lib.context_words("open_shop") == ["shop", "coins"]
    # pre-wave-1 entries (no context_words on disk) stay permissive
    lib.skills["legacy"] = {"steps": [{"type": "press", "key": "e"}], "uses": 0}
    assert lib.context_words("legacy") == []
    assert lib.context_words("nonexistent") == []


def test_skill_refuses_on_wrong_screen(monkeypatch, tmp_path) -> None:
    import asyncio

    import game_agent
    import game_goals

    monkeypatch.setattr(game_agent, "TRAJ_DIR", tmp_path)
    monkeypatch.setattr(game_goals, "SKILLS_DIR", tmp_path)

    cfg = game_agent.PlayConfig(window_title="t", game="g")
    session = game_agent.PlaySession(cfg, on_say=lambda s: asyncio.sleep(0))
    session.mind.skills.save("open_shop", [{"type": "click", "target": "shop"}],
                             context_words=["shop"])
    session.state.ocr_text = "a desert | Roll | Coins"  # wrong screen: no 'shop'

    hands_called = []

    async def fake_act(step):
        hands_called.append(step)
        return {"ok": True}

    monkeypatch.setattr(session.hands, "act", fake_act)
    asyncio.run(session._execute({"type": "skill", "name": "open_shop"}))
    assert hands_called == []                # precondition refused before the hands
    assert session.stats.refused == 1
    # and on the RIGHT screen it fires
    session.state.ocr_text = "Shop | Buy Upgrades | Coins"
    session._hwnd = None                     # skip the postcondition capture path
    asyncio.run(session._execute({"type": "skill", "name": "open_shop"}))
    assert len(hands_called) == 1


def test_skill_postcondition_records_lesson_when_nothing_changed(monkeypatch, tmp_path) -> None:
    import asyncio

    import game_agent
    import game_goals

    monkeypatch.setattr(game_agent, "TRAJ_DIR", tmp_path)
    monkeypatch.setattr(game_goals, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(game_agent, "capture_window_png", lambda hwnd: b"png")

    cfg = game_agent.PlayConfig(window_title="t", game="g")
    session = game_agent.PlaySession(cfg, on_say=lambda s: asyncio.sleep(0))
    session._hwnd = 1234
    session.state.ocr_text = "Shop | Coins"

    async def fake_ocr(png):
        return [{"text": "Shop"}, {"text": "Coins"}]  # identical text after replay

    monkeypatch.setattr(session, "_ocr_lines", fake_ocr)
    before = len(session.mind.lessons)
    asyncio.run(session._sequence_postcondition("skill 'open_shop'", session.state.ocr_text))
    assert len(session.mind.lessons) == before + 1
    assert "nothing on screen changed" in session.mind.lessons[-1]


# ── motor planner (LIMBS W1.2): plan verb + step normalization ──


def test_parse_plan_verb() -> None:
    d = parse_decision(
        "STATE: progressing\nDO: plan open the shop and buy the cheapest upgrade\nSAY: [silent]"
    )
    assert d["action"]["type"] == "plan"
    assert d["action"]["intent"].startswith("open the shop")


def test_normalize_plan_steps() -> None:
    from services.orchestrator.routes.games import normalize_plan_step as n

    assert n({"verb": "click", "target": "Shop"}) == {"type": "click", "target": "Shop"}
    assert n({"verb": "hold_click", "target": "Roll", "seconds": 3}) == {
        "type": "hold_click", "target": "Roll", "seconds": 3.0}
    assert n({"verb": "press", "target": "e"}) == {"type": "press", "key": "e"}
    assert n({"verb": "hold", "target": "w", "seconds": 20}) == {
        "type": "hold", "key": "w", "seconds": 8.0}          # clamped to rails
    assert n({"verb": "scroll_down", "target": "-"}) == {"type": "scroll", "amount": -3}
    # probe lesson 2026-07-09: the 4B put wait's duration in target
    assert n({"verb": "wait", "target": "1000"}) == {"type": "wait", "seconds": 8.0}
    assert n({"verb": "wait", "target": "2"}) == {"type": "wait", "seconds": 2.0}
    # garbage degrades to None (dropped), never to a broken action
    assert n({"verb": "click", "target": ""}) is None
    assert n({"verb": "teleport", "target": "x"}) is None


def test_plan_schema_shape() -> None:
    from services.orchestrator.routes.games import PLAN_SCHEMA, PLAN_STEP_VERBS

    assert PLAN_SCHEMA["required"] == ["steps"]
    items = PLAN_SCHEMA["properties"]["steps"]["items"]
    assert items["required"] == ["verb", "target"]
    assert set(items["properties"]["verb"]["enum"]) == set(PLAN_STEP_VERBS)
    assert PLAN_SCHEMA["properties"]["steps"]["maxItems"] == 5  # plans stay short
