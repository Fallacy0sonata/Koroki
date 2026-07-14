"""Harvester relevance gate — a title must share a DISTINCTIVE game token, not
just a generic word every game of that genre shares (RollerCoaster Tycoon 2
slipped the old any-token gate on 'tycoon', 2026-07-11)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from harvest_gameplay import game_tokens, relevance_ok


def test_generic_only_match_rejected():
    # wrong game shares only the generic word "tycoon"
    assert relevance_ok("RollerCoaster Tycoon 2 Free Play", game_tokens("theme park tycoon 2")) is False
    assert relevance_ok("Farm Simulator gameplay", game_tokens("pet simulator 99")) is False


def test_distinctive_match_accepted():
    assert relevance_ok("Theme Park Tycoon 2 VOD", game_tokens("theme park tycoon 2")) is True
    assert relevance_ok("Roblox theme park VOD", game_tokens("theme park tycoon 2")) is True
    assert relevance_ok("Pet Simulator 99 LIVE", game_tokens("pet simulator 99")) is True


def test_apostrophe_game_still_matches():
    # "sols" won't substring-match "sol's", but "rng" (distinctive) does
    assert relevance_ok("I Played Sol's RNG for 215 Days", game_tokens("sols rng")) is True


def test_unrelated_title_rejected():
    assert relevance_ok("RollerCoaster Tycoon 2", game_tokens("grow a garden")) is False


def test_no_tokens_is_permissive():
    # a game whose name has no >=3-char tokens can't be gated — allow through
    assert relevance_ok("anything at all", []) is True
