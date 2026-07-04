"""Semantic embeddings for memory recall — replaces the Phase-2B keyword placeholder.

Model: intfloat/multilingual-e5-small (384-dim, EN/TH/JA and 90+ more) via the
transformers already in the locked main stack — no new heavy dependencies. Runs on
**CPU only, always** (the GPU belongs to the Brain and TTS; a 384-dim sentence embed
costs ~20-40ms on CPU which is nothing next to LLM latency).

Design: lazy singleton; loads on first use in the calling thread; a lock serializes
forward passes (HF models aren't safe for concurrent forward). If the model can't load
(offline, disk), the singleton marks itself unavailable and callers fall back to the
old text-overlap relevance — recall NEVER breaks because embeddings are missing.

E5 requires role prefixes: "query: " for the search text, "passage: " for stored items.
Callers use embed_query()/embed_passages() and never worry about it.

Config (settings.yaml):
  mind:
    embeddings:
      enabled: true
      model: intfloat/multilingual-e5-small
"""

from __future__ import annotations

import logging
import threading

import numpy as np

from shared.utils.config import get_settings

logger = logging.getLogger("orchestrator.mind.embeddings")

_DEFAULT_MODEL = "intfloat/multilingual-e5-small"
_MAX_TOKENS = 256


class EmbeddingModel:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tok = None
        self._model = None
        self._failed = False

    # ------------------------------------------------------------------

    def available(self) -> bool:
        cfg = get_settings().get("mind", {}).get("embeddings", {})
        if not bool(cfg.get("enabled", True)):
            return False
        return not self._failed

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._failed:
            return False
        with self._lock:
            if self._model is not None:
                return True
            if self._failed:
                return False
            try:
                import torch  # noqa: F401 — verify torch import before transformers
                from transformers import AutoModel, AutoTokenizer

                cfg = get_settings().get("mind", {}).get("embeddings", {})
                name = str(cfg.get("model", _DEFAULT_MODEL))
                logger.info("loading embedding model %s (cpu)...", name)
                self._tok = AutoTokenizer.from_pretrained(name)
                self._model = AutoModel.from_pretrained(name).to("cpu").eval()
                logger.info("embedding model ready")
                return True
            except Exception as exc:
                self._failed = True
                logger.warning("embedding model unavailable (falling back to text overlap): %s", exc)
                return False

    # ------------------------------------------------------------------

    def _embed(self, texts: list[str]) -> np.ndarray | None:
        if not texts or not self.available() or not self._ensure_loaded():
            return None
        try:
            import torch

            with self._lock:
                batch = self._tok(texts, padding=True, truncation=True,
                                  max_length=_MAX_TOKENS, return_tensors="pt")
                with torch.no_grad():
                    out = self._model(**batch)
                mask = batch["attention_mask"].unsqueeze(-1).float()
                emb = (out.last_hidden_state * mask).sum(1) / mask.sum(1)
                emb = torch.nn.functional.normalize(emb, dim=-1)
            return emb.numpy().astype(np.float32)
        except Exception as exc:
            logger.warning("embed failed: %s", exc)
            return None

    def embed_query(self, text: str) -> np.ndarray | None:
        vecs = self._embed([f"query: {text}"])
        return vecs[0] if vecs is not None else None

    def embed_passages(self, texts: list[str]) -> np.ndarray | None:
        return self._embed([f"passage: {t}" for t in texts])


# ----------------------------------------------------------------------

_INSTANCE: EmbeddingModel | None = None
_INSTANCE_LOCK = threading.Lock()


def get_embedder() -> EmbeddingModel:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = EmbeddingModel()
    return _INSTANCE
