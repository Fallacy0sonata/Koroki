from services.brain import app as brain_app
from services.orchestrator.routes import chat as chat_route


def test_assistant_phrase_rewrite_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setattr(brain_app, "_assistant_phrase_rewrite_enabled", lambda: False)
    text = "How may I assist you today?"
    assert brain_app._sanitize_assistant_speak(text) == text


def test_tts_action_stripping_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setattr(chat_route, "_tts_repair_layer_enabled", lambda name, default=True: False)
    text = "*smiles* Stay close."
    assert chat_route._strip_tts_actions(text) == text


def test_sanitize_tts_speech_text_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setattr(chat_route, "_tts_repair_layer_enabled", lambda name, default=True: False)
    text = "  *smiles* Stay close.  "
    assert chat_route._sanitize_tts_speech_text(text) == "*smiles* Stay close."
