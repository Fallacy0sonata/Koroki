"""End-to-end integration of the anti-purchase chain through the REAL play loop.

De-risks the owner's live game re-test — the recurring question "does she refuse
the jetpack button?" — by driving PlaySession._cycle through the exact scenario
offline: she clicks an auto-buy button, the Robux page appears, she bans that
action forever, and the next attempt is refused before the hands ever move.

Only the I/O boundaries (window capture, vision, orchestrator, hands) are faked;
every guard under test — OCR purchase detection, consequence ledger, RuleBook
code-level block — is the real production code.
"""

import asyncio

import game_agent
import game_goals


def _build(monkeypatch, tmp_path):
    import game_knowledge

    monkeypatch.setattr(game_agent, "TRAJ_DIR", tmp_path)
    monkeypatch.setattr(game_goals, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(game_goals, "RULES_DIR", tmp_path)
    # RuleBook.add graduates to the real game card via append_lesson — stub it so
    # this test (which uses a REAL game name) never writes the live card.
    monkeypatch.setattr(game_knowledge, "append_lesson", lambda *a, **k: True)

    cfg = game_agent.PlayConfig(window_title="t", game="Sol's RNG", force_look_every=1)
    session = game_agent.PlaySession(cfg, on_say=lambda s: asyncio.sleep(0))

    hands_calls = []

    async def fake_act(action):
        hands_calls.append(action)
        return {"ok": True, "detail": "clicked"}

    monkeypatch.setattr(session, "_resolve_window", lambda: 1234)
    monkeypatch.setattr(game_agent, "capture_window_png", lambda hwnd: b"png")
    monkeypatch.setattr(session._gate, "changed", lambda png: (True, 0.5))  # never gate
    monkeypatch.setattr(session, "_describe", _scene)
    monkeypatch.setattr(session.hands, "act", fake_act)
    session._hwnd = 1234
    return session, hands_calls


async def _scene(png):
    return "a menu with buttons"


def _make_ocr(session):
    async def _ocr(png):
        # cycle 2: the Robux purchase page is on screen; else clean gameplay
        if session.stats.cycles == 2:
            return [{"text": "Special Offer"}, {"text": "Buy Now 199 Robux"}, {"text": "X"}]
        return [{"text": "Coins: 100"}, {"text": "Roll"}]
    return _ocr


def _make_decide(session):
    async def _decide(scene, mode="play"):
        if mode == "curriculum":
            return {"goal_action": "keep", "goal": ""}
        if mode == "strategy":
            return None
        # cycles 1 and 3: she reaches for the (glowing, tempting) auto-buy button
        if session.stats.cycles in (1, 3):
            return {"task_state": "progressing",
                    "action": {"type": "click", "target": "auto buy jetpack"},
                    "say": "", "raw": ""}
        return {"task_state": "progressing", "action": {"type": "look"}, "say": "", "raw": ""}
    return _decide


def test_jetpack_purchase_is_learned_and_refused(monkeypatch, tmp_path):
    session, hands_calls = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(session, "_ocr_lines", _make_ocr(session))
    monkeypatch.setattr(session, "_decide", _make_decide(session))

    async def run():
        await session._cycle()  # 1: clicks auto-buy (no page yet)
        await session._cycle()  # 2: Robux page detected -> bans the click
        await session._cycle()  # 3: tries the click again -> refused

    asyncio.run(run())

    # cycle 1's click reached the hands; cycle 3's did NOT (only one click total)
    clicks = [c for c in hands_calls if c.get("type") == "click"]
    assert len(clicks) == 1, f"the banned click should fire once, got {len(clicks)}"

    # the action is now a hard rule
    assert session.mind.rules.banned("click auto buy jetpack")
    # she refused at least once, and learned a purchase lesson
    assert session.stats.refused >= 1
    assert any("purchase" in les.lower() or "robux" in les.lower()
               for les in session.mind.lessons), session.mind.lessons
