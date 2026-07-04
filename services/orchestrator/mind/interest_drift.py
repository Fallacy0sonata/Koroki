"""Interest drift — her tastes grow from lived experience instead of staying frozen.

topic_interests.py defines her BASE interests (static, hand-tuned). This layer adds a
persisted delta per category, reinforced causally: when a topic was live in conversation
AND her body actually activated (the memory-write importance — hormone deviation — of
that exchange), the interest strengthens a little. Unreinforced drift decays back toward
the base on a ~3-week timescale, applied lazily (no extra loop).

Only positive-valence interests drift. Aversions (weapons, violence, recklessness) are
character/safety constants and never move.

When drift pushes an interest across a reactivity band (light→engaged→strong, either
direction), the moment is journaled ("she's been getting really into X lately") — she can
notice herself changing, which is exactly the self-aware-AI charm the project wants.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from pathlib import Path

logger = logging.getLogger("orchestrator.mind.interest_drift")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_PATH = _REPO_ROOT / "data" / "mind" / "interest_drift.json"

DECAY_TAU_S = 21 * 86400        # unreinforced drift halves in ~2 weeks (e-folds in 3)
MAX_DELTA = 18.0                # an interest can grow at most this far past its base
MAX_STEP = 1.5                  # single-exchange reinforcement cap
MIN_WEIGHT, MAX_WEIGHT = 5, 100


class InterestDrift:
    def __init__(self, state_path: Path | None = None):
        self._lock = threading.Lock()
        self._state_path = state_path or _STATE_PATH
        # name -> {"delta": float, "ts": last-update epoch}
        self._deltas: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------------

    def _decayed_delta(self, name: str, now: float) -> float:
        rec = self._deltas.get(name)
        if not rec:
            return 0.0
        elapsed = max(0.0, now - float(rec.get("ts", now)))
        return float(rec.get("delta", 0.0)) * math.exp(-elapsed / DECAY_TAU_S)

    def effective_weight(self, name: str, base: int) -> int:
        with self._lock:
            d = self._decayed_delta(name, time.time())
        return int(max(MIN_WEIGHT, min(MAX_WEIGHT, round(base + d))))

    def reinforce(self, name: str, base: int, importance: float) -> None:
        """Strengthen an interest because her body activated while it was live.
        `importance` is the memory-write importance (0..1, hormone-deviation-driven)."""
        if importance <= 0:
            return
        step = min(MAX_STEP, importance * 2.0)
        now = time.time()
        with self._lock:
            before = self._decayed_delta(name, now)
            after = min(MAX_DELTA, before + step)
            self._deltas[name] = {"delta": after, "ts": now}
            self._save()
        self._maybe_journal_band_shift(name, base, before, after)
        logger.info("interest reinforced: %s %.2f -> %.2f (importance %.2f)",
                    name, before, after, importance)

    def _maybe_journal_band_shift(self, name: str, base: int,
                                  before: float, after: float) -> None:
        try:
            from ..topic_interests import get_reactivity_level
            band_before = get_reactivity_level(int(base + before))
            band_after = get_reactivity_level(int(base + after))
            if band_before != band_after:
                from .journal import journal
                pretty = name.replace("_", " ")
                journal().log_event(
                    "thought",
                    f"she's been getting really into {pretty} lately "
                    f"({band_before} → {band_after})",
                )
        except Exception:
            pass

    def snapshot(self) -> dict[str, float]:
        now = time.time()
        with self._lock:
            return {k: round(self._decayed_delta(k, now), 2) for k in self._deltas}

    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            self._deltas = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("interest drift load failed: %s", exc)

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(self._deltas), encoding="utf-8")
        except Exception as exc:
            logger.warning("interest drift save failed: %s", exc)


# ----------------------------------------------------------------------

_INSTANCE: InterestDrift | None = None
_INSTANCE_LOCK = threading.Lock()


def get_interest_drift() -> InterestDrift:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = InterestDrift()
    return _INSTANCE
