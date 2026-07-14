"""Exact-term hybrid recall layer (mind/memory.py, 2026-07-06)."""

from services.orchestrator.mind.memory import _exact_term_relevance


def test_exact_gamer_tag_hits():
    r = _exact_term_relevance(
        "do you remember xX_Fallacy0_Xx from the raid?",
        "played valorant with xx_fallacy0_xx, he kept whiffing",
    )
    assert r >= 0.55


def test_stopwords_do_not_floor():
    assert _exact_term_relevance("do you know what that was", "she was thinking about rain") == 0.0


def test_cjk_substring_match():
    r = _exact_term_relevance("あの夜に約束したこと覚えてる?", "配信の後で約束した — 歌を練習するって")
    assert r >= 0.55


def test_no_hit_returns_zero():
    assert _exact_term_relevance("tell me about quantum stuff", "she likes strawberry cake") == 0.0


def test_partial_hits_scale_up():
    one = _exact_term_relevance("cockatiel", "the cockatiel screamed at 6am")
    two = _exact_term_relevance("cockatiel scream", "the cockatiel screamed at 6am")
    assert 0.55 <= one <= two <= 1.0


def test_empty_inputs():
    assert _exact_term_relevance("", "content") == 0.0
    assert _exact_term_relevance("query", "") == 0.0
