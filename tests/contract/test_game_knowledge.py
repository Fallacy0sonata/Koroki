"""Game knowledge cards (GM2 step 2) — registry resolution + card lifecycle."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import game_knowledge as gk


@pytest.fixture()
def knowledge_dir(tmp_path, monkeypatch):
    know = tmp_path / "knowledge"
    monkeypatch.setattr(gk, "_KNOW_DIR", know)
    monkeypatch.setattr(gk, "_REGISTRY", know / "registry.json")
    know.mkdir(parents=True)
    (know / "registry.json").write_text(json.dumps({
        "games": [
            {"slug": "sols_rng", "platform": "roblox", "display": "Sol's RNG",
             "aliases": ["sol", "sol rng", "sols", "sol's rng"]},
            {"slug": "forza_horizon_6", "platform": "pc", "display": "Forza Horizon 6",
             "aliases": ["forza", "fh6"]},
        ]
    }))
    card_dir = know / "roblox"
    card_dir.mkdir()
    (card_dir / "sols_rng.md").write_text(
        "# Sol's RNG\n\n## For her\nIdle luck game, auras are flex, not combat.\n\n"
        "## Notes\nlong form\n\n## Lessons\n",
        encoding="utf-8",
    )
    return know


def test_resolve_exact_alias(knowledge_dir):
    assert gk.resolve("sol")["slug"] == "sols_rng"
    assert gk.resolve("SOL RNG")["slug"] == "sols_rng"
    assert gk.resolve("fh6")["slug"] == "forza_horizon_6"


def test_resolve_fuzzy_and_containment(knowledge_dir):
    # punctuation/spacing noise
    assert gk.resolve("sol's  rng!!")["slug"] == "sols_rng"
    # containment: partial mention
    assert gk.resolve("forza horizon")["slug"] == "forza_horizon_6"
    # fuzzy typo
    assert gk.resolve("sols rmg")["slug"] == "sols_rng"


def test_resolve_unknown_returns_none(knowledge_dir):
    assert gk.resolve("elden ring") is None
    assert gk.resolve("") is None


def test_prompt_summary_extracts_for_her_only(knowledge_dir):
    s = gk.prompt_summary("sol")
    assert "Idle luck game" in s
    assert "long form" not in s
    assert len(s) <= 600


def test_ensure_card_creates_unknown_game(knowledge_dir):
    entry = gk.ensure_card("Elden Ring", platform="pc")
    assert entry["slug"] == "elden_ring"
    p = gk.card_path(entry)
    assert p.exists()
    assert "never touch anything" in p.read_text(encoding="utf-8")
    # registered: now resolvable
    assert gk.resolve("elden ring")["slug"] == "elden_ring"
    # idempotent — same entry back, no duplicate
    again = gk.ensure_card("elden ring")
    assert again["slug"] == "elden_ring"
    reg = json.loads((knowledge_dir / "registry.json").read_text())
    assert len([g for g in reg["games"] if g["slug"] == "elden_ring"]) == 1


def test_append_lesson_persists_and_dedupes(knowledge_dir):
    assert gk.append_lesson("sol", "the star button opens the shop — harmless")
    text = gk.card_path(gk.resolve("sol")).read_text(encoding="utf-8")
    assert "star button" in text
    # dedupe
    assert gk.append_lesson("sol", "the star button opens the shop — harmless")
    text2 = gk.card_path(gk.resolve("sol")).read_text(encoding="utf-8")
    assert text2.count("star button") == 1


def test_lesson_lands_in_lessons_section_not_for_her(knowledge_dir):
    gk.append_lesson("sol", "new lesson here")
    text = gk.card_path(gk.resolve("sol")).read_text(encoding="utf-8")
    for_her = text.split("## Notes")[0]
    assert "new lesson here" not in for_her
