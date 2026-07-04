"""Contract tests for the vision service (sight) — engine stubbed, no GPU/model."""

import asyncio
import base64

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import services.vision.main as vision_main
from services.orchestrator.schemas import ChatRequest, UserContext


def _b64(data: bytes = b"fake-image-bytes") -> str:
    return base64.b64encode(data).decode("ascii")


class FakeEngine:
    def __init__(self):
        self.model_loaded = False
        self.load_count = 0
        self.last_question = "UNSET"
        self.last_max_tokens = None
        self.unload_called = False

    def describe(self, images, question=None, max_tokens=None):
        self.last_question = question
        self.last_max_tokens = max_tokens
        self.model_loaded = True
        self.load_count += 1
        return [f"description of image {i + 1}" for i in range(len(images))]

    def unload(self):
        self.unload_called = True
        was = self.model_loaded
        self.model_loaded = False
        return was


@pytest.fixture()
def vision_client(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(vision_main, "_engine", engine)
    monkeypatch.setattr(vision_main, "_game_session", None)
    monkeypatch.setattr(
        vision_main,
        "get_settings",
        lambda: {
            "vision": {
                "enabled": True,
                "model_dir": "tools/models/moondream2-test",
                "quant": "int4",
                "unload_after_describe": True,
            }
        },
    )
    return TestClient(vision_main.app), engine


# ── service endpoints ────────────────────────────────────────────────


def test_health_ready_version(vision_client):
    client, engine = vision_client
    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready").json()
    assert ready["ready"] is True
    assert ready["model_loaded"] is False  # lazy: ready before any look
    version = client.get("/version").json()
    assert version["service"] == "vision"


def test_describe_single_image(vision_client):
    client, engine = vision_client
    resp = client.post("/v1/describe", json={"images_b64": [_b64()]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["description"] == "description of image 1"
    assert body["was_cold_load"] is True
    assert body["game"] is None
    assert engine.last_question is None  # no question → caption mode
    # Sporadic-look mode: VRAM released right after the look (background task).
    assert engine.unload_called is True


def test_describe_two_images_joined(vision_client):
    client, _ = vision_client
    resp = client.post("/v1/describe", json={"images_b64": [_b64(), _b64(b"other")]})
    assert resp.status_code == 200
    desc = resp.json()["description"]
    assert "image 1:" in desc and "image 2:" in desc


def test_describe_rejects_bad_base64(vision_client):
    client, _ = vision_client
    resp = client.post("/v1/describe", json={"images_b64": ["not-valid-b64!!!"]})
    assert resp.status_code == 400


def test_describe_rejects_too_many_images(vision_client):
    client, _ = vision_client
    resp = client.post("/v1/describe", json={"images_b64": [_b64()] * 3})
    assert resp.status_code == 422


def test_game_session_conditions_describe(vision_client):
    client, engine = vision_client
    enter = client.post(
        "/v1/game/enter",
        json={"game": "Minecraft", "facts": ["hearts bar = health"]},
    )
    assert enter.status_code == 200
    state = client.get("/v1/game").json()
    assert state["active"] is True and state["game"] == "Minecraft"

    resp = client.post("/v1/describe", json={"images_b64": [_b64()]})
    assert resp.json()["game"] == "Minecraft"
    # No explicit question → the game context still becomes the framing question.
    assert "Minecraft" in engine.last_question
    assert "hearts bar" in engine.last_question
    # Game sessions keep the model resident (continuous frames incoming).
    assert engine.unload_called is False

    client.post("/v1/game/exit")
    assert client.get("/v1/game").json()["active"] is False
    client.post("/v1/describe", json={"images_b64": [_b64()]})
    assert engine.last_question is None  # back to plain caption


def test_unload_endpoint(vision_client):
    client, engine = vision_client
    engine.model_loaded = True  # simulate a resident model (e.g. game session)
    resp = client.post("/v1/unload")
    assert resp.json()["unloaded"] is True
    assert engine.unload_called
    # Idempotent: a second unload reports nothing to do.
    assert client.post("/v1/unload").json()["unloaded"] is False


# ── ChatRequest schema (orchestrator boundary) ───────────────────────


def _chat_request(**extra):
    return ChatRequest(
        request_id="test_req",
        message="look at this",
        user_context=UserContext(user_id="u1", relationship_score=50, is_owner=False),
        **extra,
    )


def test_chat_request_accepts_images():
    req = _chat_request(images_b64=[_b64(), _b64()])
    assert len(req.images_b64) == 2


def test_chat_request_rejects_three_images():
    with pytest.raises(ValidationError):
        _chat_request(images_b64=[_b64()] * 3)


def test_chat_request_rejects_empty_image():
    with pytest.raises(ValidationError):
        _chat_request(images_b64=[""])


def test_chat_request_images_default_none():
    assert _chat_request().images_b64 is None


# ── senses client fail-soft contract ─────────────────────────────────


def test_senses_client_disabled_returns_none(monkeypatch):
    from services.orchestrator.senses import vision as senses_vision

    monkeypatch.setattr(
        senses_vision, "get_settings", lambda: {"vision": {"enabled": False}}
    )
    result = asyncio.run(senses_vision.describe_images([_b64()], request_id="t"))
    assert result is None


def test_senses_client_unreachable_returns_none(monkeypatch):
    from services.orchestrator.senses import vision as senses_vision

    monkeypatch.setattr(
        senses_vision,
        "get_settings",
        lambda: {
            "vision": {"enabled": True, "describe_timeout_seconds": 1},
            "services": {"vision": {"url": "http://127.0.0.1:1"}},  # nothing listens
        },
    )
    result = asyncio.run(senses_vision.describe_images([_b64()], request_id="t"))
    assert result is None
