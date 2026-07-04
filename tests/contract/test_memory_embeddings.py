"""Contract tests for semantic memory recall (mind/embeddings + mind/memory vectors).

Uses a deterministic fake embedder — the real model is exercised manually (it needs a
470MB download); these tests pin the WIRING: vector storage, cosine relevance, fallback.
"""

import json
import time

import numpy as np

from services.orchestrator.mind import embeddings as emb_mod
from services.orchestrator.mind.memory import MemoryStream


class FakeEmbedder:
    """Deterministic 3-dim embedder: axis 0 = 'sing', axis 1 = 'rain', axis 2 = other."""

    def __init__(self, available: bool = True):
        self._available = available

    def available(self) -> bool:
        return self._available

    def _vec(self, text: str) -> np.ndarray:
        t = text.lower()
        v = np.array([
            2.0 if "sing" in t or "song" in t else 0.1,
            2.0 if "rain" in t or "weather" in t else 0.1,
            0.5,
        ])
        return (v / np.linalg.norm(v)).astype(np.float32)

    def embed_query(self, text: str):
        return self._vec(text)

    def embed_passages(self, texts):
        return np.stack([self._vec(t) for t in texts])


def _wait_for_vectors(stream: MemoryStream, n: int, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() - deadline < 0:
        with stream._lock:
            if len(stream._vectors) >= n:
                return
        time.sleep(0.02)
    raise AssertionError(f"vectors never reached {n}")


def test_semantic_relevance_beats_keyword(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(emb_mod, "_INSTANCE", FakeEmbedder())
    stream = MemoryStream(stream_path=tmp_path / "stream.jsonl",
                          vectors_path=tmp_path / "vectors.jsonl")
    stream.remember("she practiced a new song for an hour", importance=0.6)
    stream.remember("it rained all afternoon and the window fogged up", importance=0.6)
    _wait_for_vectors(stream, 2)

    top = stream.retrieve("what music has she been singing?", top_k=1)
    assert top, "no recall results"
    assert "song" in top[0].node.content
    # semantic relevance should clearly separate the two (calibrated 0..1)
    assert top[0].score_relevance > 0.5


def test_fallback_to_text_overlap_when_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(emb_mod, "_INSTANCE", FakeEmbedder(available=False))
    stream = MemoryStream(stream_path=tmp_path / "stream.jsonl",
                          vectors_path=tmp_path / "vectors.jsonl")
    stream.remember("she practiced a new song for an hour", importance=0.6)
    top = stream.retrieve("song practice", top_k=1)
    assert top and "song" in top[0].node.content  # Jaccard fallback still works


def test_vector_sidecar_persists_across_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(emb_mod, "_INSTANCE", FakeEmbedder())
    stream = MemoryStream(stream_path=tmp_path / "stream.jsonl",
                          vectors_path=tmp_path / "vectors.jsonl")
    stream.remember("she sang quietly to herself", importance=0.7)
    _wait_for_vectors(stream, 1)

    # simulate restart: fresh instance must load vectors from the sidecar
    stream2 = MemoryStream(stream_path=tmp_path / "stream.jsonl",
                           vectors_path=tmp_path / "vectors.jsonl")
    with stream2._lock:
        assert len(stream2._vectors) == 1
    lines = (tmp_path / "vectors.jsonl").read_text(encoding="utf-8").strip().splitlines()
    rec = json.loads(lines[0])
    assert "id" in rec and len(rec["v"]) == 3


def test_backfill_embeds_preexisting_nodes(tmp_path, monkeypatch) -> None:
    # write nodes with embeddings unavailable → no vectors
    monkeypatch.setattr(emb_mod, "_INSTANCE", FakeEmbedder(available=False))
    stream = MemoryStream(stream_path=tmp_path / "stream.jsonl",
                          vectors_path=tmp_path / "vectors.jsonl")
    stream.remember("an old memory about a song", importance=0.6)
    with stream._lock:
        assert not stream._vectors

    # embeddings become available; a new instance backfills (call directly — the
    # thread sleeps 30s by design)
    monkeypatch.setattr(emb_mod, "_INSTANCE", FakeEmbedder())
    stream2 = MemoryStream(stream_path=tmp_path / "stream.jsonl",
                           vectors_path=tmp_path / "vectors.jsonl")
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    stream2._backfill_vectors()
    with stream2._lock:
        assert len(stream2._vectors) == 1
