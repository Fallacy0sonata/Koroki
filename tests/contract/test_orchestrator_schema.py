from services.orchestrator.schemas import ChatRequest


def test_valid_chat_request() -> None:
    payload = {
        "request_id": "req-1",
        "message": "hello",
        "user_context": {
            "user_id": "u1",
            "relationship_score": 30,
            "is_owner": False,
            "mode": "auto",
            "platform": "discord",
        },
    }
    obj = ChatRequest.model_validate(payload)
    assert obj.user_context.relationship_score == 30


def test_rejects_invalid_relationship_score() -> None:
    payload = {
        "request_id": "req-1",
        "message": "hello",
        "user_context": {
            "user_id": "u1",
            "relationship_score": 999,
            "is_owner": False,
            "mode": "auto",
            "platform": "discord",
        },
    }

    try:
        ChatRequest.model_validate(payload)
        assert False, "Expected validation error"
    except Exception:
        assert True


# Owner rule (2026-07-03): nothing inside *...* may ever be SPOKEN. Multi-word starred
# spans are always stage directions; only single-word emphasis stays in speech.
def test_multiword_star_spans_never_spoken() -> None:
    from services.orchestrator.routes.chat import _is_action_span, _strip_tts_actions

    assert _is_action_span("small smile")
    assert _is_action_span("brief goodbye")
    assert _is_action_span("smiles softly")
    assert not _is_action_span("truly")

    spoken = _strip_tts_actions("good. *small smile* always good actually")
    assert "small smile" not in spoken
    spoken = _strip_tts_actions("okay. sleep well. *brief goodbye*")
    assert "brief goodbye" not in spoken
