"""GM2 step 4 — the consequence ledger: rules persist, bind, and graduate."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import game_goals as gg
import game_knowledge as gk


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(gg, "RULES_DIR", tmp_path / "rules")
    monkeypatch.setattr(gg, "SKILLS_DIR", tmp_path / "skills")
    know = tmp_path / "knowledge"
    monkeypatch.setattr(gk, "_KNOW_DIR", know)
    monkeypatch.setattr(gk, "_REGISTRY", know / "registry.json")
    return tmp_path


def test_rule_learned_once_and_persisted(sandbox):
    rb = gg.RuleBook("tycoon_test", "Tycoon Test")
    assert rb.add("click auto buy", "never press 'auto buy' — robux gamepass", "DANGEROUS")
    # dedupe: same pattern doesn't duplicate
    assert rb.add("Click Auto  Buy!", "never press 'auto buy' — robux gamepass")
    assert len(rb.rules) == 1
    # persisted: a fresh RuleBook (new session) still knows
    rb2 = gg.RuleBook("tycoon_test", "Tycoon Test")
    assert len(rb2.rules) == 1


def test_banned_matches_normalized_target(sandbox):
    rb = gg.RuleBook("t2", "T2")
    rb.add("click jetpack", "never press jetpack — robux")
    assert rb.banned("click jetpack button") is not None
    assert rb.banned("CLICK  JETPACK!!") is not None
    assert rb.banned("click collect cash") is None
    # hits counted
    assert rb.rules[0]["hits"] == 2


def test_rule_graduates_into_game_card(sandbox):
    gk.ensure_card("Tycoon Test", platform="roblox")
    rb = gg.RuleBook("tycoon_test", "Tycoon Test")
    rb.add("click auto buy", "never press 'auto buy' — robux gamepass")
    card = gk.card_path(gk.resolve("Tycoon Test")).read_text(encoding="utf-8")
    assert "RULE: never press 'auto buy'" in card


def test_rules_render_in_prompt_blocks(sandbox):
    mind = gg.GameMind(game="Tycoon Test", genre="tycoon")
    mind.rules.add("click auto buy", "never press 'auto buy' — robux gamepass")
    blocks = mind.prompt_blocks()
    assert "HARD RULES" in blocks["lessons"]
    assert "auto buy" in blocks["lessons"]


def test_outcome_log_last_action():
    log = gg.OutcomeLog()
    assert log.last_action() is None
    log.action_taken("click jetpack")
    assert log.last_action() == "click jetpack"
    log.observe_effect(screen_changed=True, metric_deltas=[])
    assert log.last_action() == "click jetpack"


def test_purchase_detector_signatures(sandbox, monkeypatch):
    from game_agent import PlaySession, PlayConfig

    async def _noop(_):
        pass

    session = PlaySession(PlayConfig(window_title="w", game="Tycoon Test",
                                     genre="tycoon"), _noop)
    assert session._detect_purchase("a shop menu with a Buy Now button", None)
    assert session._detect_purchase(None, [{"text": "Get Robux"}])
    assert session._detect_purchase("R$ 499", [])
    assert not session._detect_purchase("a factory with conveyor belts", [{"text": "Cash: 5,210"}])


def test_purchase_page_bans_the_suspect(sandbox):
    from game_agent import PlaySession, PlayConfig

    async def _noop(_):
        pass

    session = PlaySession(PlayConfig(window_title="w", game="Tycoon Test",
                                     genre="tycoon"), _noop)
    session.mind.outcomes.action_taken("click auto buy")
    session._handle_purchase_page()
    assert session.mind.rules.banned("click auto buy") is not None
    # and the lesson is in her working memory
    assert any("purchase" in l.lower() for l in session.mind.lessons)
