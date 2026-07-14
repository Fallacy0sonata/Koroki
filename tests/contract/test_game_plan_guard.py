"""Offline integration of the MOTOR-PLAN closed loop through the real executor.

De-risks the plan-verb path for the owner's live test the same way the jetpack
test did for direct clicks: the planner's steps go through the SAME rails —
per-step RuleBook guard and a purchase tripwire BETWEEN steps — so a bad plan
can't run to completion. Only the planner HTTP and I/O boundaries are faked.
"""

import asyncio

import game_agent
import game_goals


def _build(monkeypatch, tmp_path):
    import game_knowledge

    monkeypatch.setattr(game_agent, "TRAJ_DIR", tmp_path)
    monkeypatch.setattr(game_goals, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(game_goals, "RULES_DIR", tmp_path)
    monkeypatch.setattr(game_knowledge, "append_lesson", lambda *a, **k: True)
    monkeypatch.setattr(game_agent, "capture_window_png", lambda hwnd: b"png")

    cfg = game_agent.PlayConfig(window_title="t", game="Sol's RNG")
    session = game_agent.PlaySession(cfg, on_say=lambda s: asyncio.sleep(0))
    session._hwnd = 1234
    hands_calls = []

    async def fake_act(action):
        hands_calls.append(action)
        return {"ok": True, "detail": "did it"}

    monkeypatch.setattr(session.hands, "act", fake_act)
    return session, hands_calls


def test_plan_aborts_when_purchase_page_appears_mid_plan(monkeypatch, tmp_path):
    session, hands_calls = _build(monkeypatch, tmp_path)

    async def fake_plan(intent):
        return [{"type": "click", "target": "Shop"},
                {"type": "click", "target": "Upgrades"},
                {"type": "click", "target": "Confirm"}]

    monkeypatch.setattr(session, "_fetch_plan", fake_plan)

    # after the FIRST step, a Robux page appears; it must abort before step 2's OCR
    ocr_seq = iter([
        [{"text": "Special Offer"}, {"text": "Buy Now 99 Robux"}, {"text": "X"}],  # after step 1
        [{"text": "Shop"}],                                                          # (unused)
    ])

    async def fake_ocr(png):
        try:
            return next(ocr_seq)
        except StopIteration:
            return [{"text": "Shop"}]

    monkeypatch.setattr(session, "_ocr_lines", fake_ocr)
    asyncio.run(session._execute_plan("open the shop and buy an upgrade"))

    # only the first step ran; the tripwire stopped the rest
    assert len(hands_calls) == 1
    assert session._purchase_active is True
    assert any("purchase" in l.lower() or "robux" in l.lower() for l in session.mind.lessons)


def test_plan_step_refused_by_learned_rule(monkeypatch, tmp_path):
    session, hands_calls = _build(monkeypatch, tmp_path)
    # she already learned this action opens a purchase page
    session.mind.rules.add("click auto buy", "never press it — opens Robux", klass="DANGEROUS")

    async def fake_plan(intent):
        return [{"type": "click", "target": "Roll"},
                {"type": "click", "target": "auto buy"},   # banned — must refuse here
                {"type": "click", "target": "Collect"}]

    async def fake_ocr(png):
        return [{"text": "Roll"}, {"text": "Coins"}]  # never a purchase page

    monkeypatch.setattr(session, "_fetch_plan", fake_plan)
    monkeypatch.setattr(session, "_ocr_lines", fake_ocr)
    asyncio.run(session._execute_plan("roll and grab the auto buy"))

    # step 1 ran; step 2 (banned) refused before hands; step 3 never reached
    assert [c["target"] for c in hands_calls] == ["Roll"]
    assert session.stats.refused >= 1


def test_clean_plan_runs_all_steps(monkeypatch, tmp_path):
    session, hands_calls = _build(monkeypatch, tmp_path)

    async def fake_plan(intent):
        return [{"type": "click", "target": "Build"}, {"type": "click", "target": "Path"}]

    async def fake_ocr(png):
        return [{"text": "Build"}, {"text": "Money: 500"}]  # benign, changes are fine

    monkeypatch.setattr(session, "_fetch_plan", fake_plan)
    monkeypatch.setattr(session, "_ocr_lines", fake_ocr)
    asyncio.run(session._execute_plan("build a path"))

    assert [c["target"] for c in hands_calls] == ["Build", "Path"]
    assert session._purchase_active is False


def test_planner_transport_failure_is_safe(monkeypatch, tmp_path):
    session, hands_calls = _build(monkeypatch, tmp_path)

    async def fake_plan(intent):
        return None  # transport failure

    monkeypatch.setattr(session, "_fetch_plan", fake_plan)
    asyncio.run(session._execute_plan("do something"))
    assert hands_calls == []  # nothing runs on a failed plan
