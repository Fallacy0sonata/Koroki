from services.orchestrator.rag.engine import _should_search_for_query


def test_rag_triggers_for_current_release_lookup() -> None:
    assert _should_search_for_query("What is the latest Hades 2 update?")


def test_rag_skips_general_opinion_chat() -> None:
    assert not _should_search_for_query("What do you think about spicy food?")


def test_rag_skips_simple_greeting() -> None:
    assert not _should_search_for_query("hello there")
