"""
Reward Prediction Error (RPE) engine.

Schultz's foundational finding (Hollerman & Schultz 1998, Schultz 2016):
dopamine neurons emit a temporal-difference reward prediction error signal.

We implement the classic TD update:
    δ_t = r_t + γ·V(s_{t+1}) - V(s_t)
    V(s_t) ← V(s_t) + α·V·δ_t

δ is the dopamine phasic signal. Positive δ → spike. Negative δ → dip below tonic.

Why this matters for Koroki:
  - Expecting +5 and getting +3 releases NEGATIVE δ → quiet disappointment
  - Surprised by +1 when expecting nothing releases positive δ → delight
  - Threat resolved without harm → relief = positive δ (releases oxytocin + dopamine)
  - This is what gives disappointment ≠ relief ≠ surprise their distinct textures

The state representation is intentionally simple — string keys identifying
"states" (e.g., "user_typing", "owner_present", "long_silence"). V(s) is
learned online from observed rewards. We don't need rich state features
because the body/endocrine subsystem provides the rest of the texture.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("orchestrator.mind.rpe")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_PATH = _REPO_ROOT / "data" / "body" / "rpe_state.json"


class RPESystem:
    """TD-learning reward prediction error system.

    Single-instance, threadsafe via internal lock. State persists to disk
    so V estimates survive process restarts.
    """

    def __init__(
        self,
        alpha: float = 0.3,
        gamma: float = 0.9,
        state_path: Path | None = None,
    ) -> None:
        # alpha=0.3 (not the textbook 0.1): for an agentic captain we want
        # expectations to form within ~3-5 repetitions, not 20+. Real biology
        # builds slower, but Koroki's "learning rate for what to expect from
        # a person" should be more responsive than a rat's foraging signal.
        self.alpha = alpha
        self.gamma = gamma
        self._V: dict[str, float] = {}
        self._last_state: str | None = None
        self._lock = threading.Lock()
        self._state_path = state_path or _STATE_PATH
        self._load()

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def expect(self, state_key: str) -> float:
        """Current expected value of being in `state_key`. Defaults to 0."""
        with self._lock:
            return self._V.get(state_key, 0.0)

    def observe(self, state_key: str, reward: float) -> float:
        """Process a new (state, reward) observation. Returns δ (RPE).

        Convention:
          - `state_key` describes the situation now (e.g. "owner_messaged_warm")
          - `reward` is the immediate reward signal of arriving in this state
          - V is updated via TD-learning: V(prev) += α·δ

        The returned δ is what the dopamine phasic system reads. Positive δ
        spikes dopamine; negative δ dips it below tonic. Magnitude carries
        the "how unexpected" texture.
        """
        with self._lock:
            v_now = self._V.get(state_key, 0.0)
            v_prev = self._V.get(self._last_state, 0.0) if self._last_state else 0.0
            delta = reward + self.gamma * v_now - v_prev
            if self._last_state is not None:
                self._V[self._last_state] = v_prev + self.alpha * delta
            self._last_state = state_key
        return delta

    def observe_relief(self, expected_negative_value: float) -> float:
        """Threat resolved without the predicted harm. Returns relief δ > 0.

        Use case: Koroki had predicted aversive outcome (V_neg = -0.4) but it
        didn't happen. δ_relief = 0 - V_neg = +0.4 → simultaneous oxytocin
        nudge + dopamine spike → "phew, okay" feeling.
        """
        return -expected_negative_value  # negative of negative = positive δ

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._V = dict(data.get("V", {}))
            self._last_state = data.get("last_state")
            logger.info(
                "RPE loaded: %d states tracked, last_state=%s",
                len(self._V),
                self._last_state,
            )
        except Exception as exc:
            logger.warning("RPE state load failed: %s — starting fresh", exc)

    def save(self) -> None:
        """Persist V estimates and last_state. Call periodically + on shutdown."""
        with self._lock:
            payload: dict[str, Any] = {
                "V": dict(self._V),
                "last_state": self._last_state,
            }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("RPE state save failed: %s", exc)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Diagnostic snapshot of current state. Not for LLM consumption."""
        with self._lock:
            return {
                "n_states_tracked": len(self._V),
                "last_state": self._last_state,
                "alpha": self.alpha,
                "gamma": self.gamma,
            }


# Module-level singleton — accessed via get_rpe() to ensure single instance.
_INSTANCE: RPESystem | None = None
_INSTANCE_LOCK = threading.Lock()


def get_rpe() -> RPESystem:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = RPESystem()
    return _INSTANCE
