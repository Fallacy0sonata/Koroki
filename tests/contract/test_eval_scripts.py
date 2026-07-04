from scripts.eval import behavior_eval


def test_behavior_eval_detects_assistant_phrasing() -> None:
    result = behavior_eval._check_no_assistant_phrasing(
        "How may I assist you today?",
        anti_terms=["assist"],
    )
    assert not result.passed


def test_behavior_eval_discord_contract_limits() -> None:
    checks = behavior_eval._check_discord_contract(
        "One. Two. Three. Four.",
        max_chars=100,
        max_sentences=3,
        max_action_markers=1,
    )
    assert any(check.name == "discord_sentence_budget" and not check.passed for check in checks)


def test_behavior_eval_first_sentence_overlap() -> None:
    result = behavior_eval._check_first_sentence(
        "pick tea or coffee and tell me fast",
        "Tea, obviously. It is cleaner tonight.",
    )
    assert result.passed
