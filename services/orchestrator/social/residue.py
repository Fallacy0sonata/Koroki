"""
Emotional residue — what's left over from an interaction after it ends.

Per atlas §6.2. The intuition: argument with someone today → cortisol baseline
raised slightly for hours. Warm exchange → oxytocin baseline lifted briefly.
Next session with same user starts with this residue still present.

Architecture:
  - Per-user residue: dict of (hormone → magnitude) plus last_event_ts
  - Residue decays exponentially with tau ~ 6 hours
  - When user reappears (chat handler), residue is INJECTED back into endocrine
    as a body event with low intensity
  - This way, "feelings about a person" persist across sessions

State per user:
  - residue: {hormone_name: float} — e.g. {cortisol: 0.15, oxytocin: 0.05}
  - last_event_ts: when residue was last updated (for decay)
  - last_session_summary: short prose hint for diagnostics

Decay model:
  - Each tick or read, apply: residue *= exp(-dt / RESIDUE_DECAY_TAU_HOURS / 3600)
  - Decays toward zero — doesn't persist forever
  - Major events refresh the residue magnitude

═══════════════════════════════════════════════════════════════════════════════
🐛 PREDICTED BUGS:

RD1. "Residue from yesterday's conflict has no effect today."
    Look at: get_residue_for_user — verify decay is computed but doesn't zero
    out too aggressively. With 6h tau, after 24h residue is ~2% of original.
    May want longer tau for important events.

RD2. "Every interaction immediately spikes residue."
    Look at: write_residue magnitudes. Should accumulate slowly, not stamp
    a fresh max each event.

RD3. "Owner's residue feels indistinguishable from a stranger's."
    Look at: chat.py integration. The injection event should be tagged with
    owner_present where applicable so endocrine reacts proportionally.

RD4. "Residue file grows unbounded."
    Look at: residue tracking per-user means file scales with users.
    For now we only track owner + active test users (~2-5). At scale, we'd
    purge users not seen in >30 days.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

logger = logging.getLogger("orchestrator.social.residue")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_PATH = _REPO_ROOT / "data" / "social" / "residue.json"

# Residue decays exponentially with this time constant.
RESIDUE_DECAY_TAU_HOURS = 6.0  # ~6 hours half-life-ish

# Max residue per hormone (prevents accumulation runaway)
RESIDUE_MAX = {
    "cortisol": 0.25,
    "oxytocin": 0.20,
    "dopamine_phasic": 0.10,
    "norepinephrine": 0.15,
}


@dataclass
class UserResidue:
    user_id: str
    residue: dict[str, float] = field(default_factory=dict)
    last_event_ts: float = 0.0
    last_session_summary: str = ""


class ResidueManager:
    """Per-user emotional aftermath, persisted across sessions."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, UserResidue] = {}
        self._state_path = state_path or _STATE_PATH
        self._load()

    # ─── Read API ───

    def get_residue_for_user(self, user_id: str,
                              now_ts: float | None = None) -> dict[str, float]:
        """Get current residue (with decay applied) without modifying state."""
        ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            r = self._state.get(user_id)
            if not r:
                return {}
            if r.last_event_ts <= 0 or not r.residue:
                return {}
            hours_elapsed = (ts - r.last_event_ts) / 3600.0
            decay_factor = math.exp(-hours_elapsed / RESIDUE_DECAY_TAU_HOURS)
            return {h: m * decay_factor for h, m in r.residue.items()}

    # ─── Write API ───

    def write_residue(self, user_id: str, hormone: str, delta: float,
                       summary_hint: str = "") -> None:
        """Add to a user's residue for a specific hormone.

        delta is the magnitude to ADD (or subtract if negative). Capped per hormone.
        """
        ts = time.time()
        with self._lock:
            r = self._state.get(user_id)
            if not r:
                r = UserResidue(user_id=user_id)
                self._state[user_id] = r
            # Apply decay first (so multiple writes accumulate with time)
            if r.last_event_ts > 0:
                hours_elapsed = (ts - r.last_event_ts) / 3600.0
                decay_factor = math.exp(-hours_elapsed / RESIDUE_DECAY_TAU_HOURS)
                r.residue = {h: m * decay_factor for h, m in r.residue.items()}
            # Add new delta
            current = r.residue.get(hormone, 0.0)
            max_for_hormone = RESIDUE_MAX.get(hormone, 0.20)
            r.residue[hormone] = max(-max_for_hormone,
                                       min(max_for_hormone, current + delta))
            r.last_event_ts = ts
            if summary_hint:
                r.last_session_summary = summary_hint
        self._save()

    def apply_residue_to_endocrine(self, user_id: str,
                                     endocrine_ingest_callable,
                                     Event_class,
                                     is_owner: bool = False) -> bool:
        """When a user reappears, inject their residue back into endocrine.

        This is what makes "feelings about a person" persist across sessions.
        Should be called once per session-start (not per message).

        Returns True if residue was non-trivial and applied.
        """
        residue = self.get_residue_for_user(user_id)
        if not residue:
            return False

        tags = ["residue", "session_start"]
        if is_owner:
            tags.append("owner_present")

        applied_any = False
        for hormone, magnitude in residue.items():
            if abs(magnitude) < 0.02:
                continue
            # Build an event that nudges this specific hormone via valence/intensity.
            # Cortisol pre-load → negative valence event. Oxytocin pre-load → positive.
            if hormone == "cortisol" and magnitude > 0:
                endocrine_ingest_callable(Event_class(
                    type=f"residue:tense",
                    source=user_id,
                    valence=-0.3,
                    intensity=magnitude * 2,  # scale up for visible effect
                    tags=tags + ["memory_echo"],
                    skip_rpe=True,
                ))
                applied_any = True
            elif hormone == "oxytocin" and magnitude > 0:
                endocrine_ingest_callable(Event_class(
                    type=f"residue:warm",
                    source=user_id,
                    valence=0.5,
                    intensity=magnitude * 2.5,
                    tags=tags + ["affectionate", "memory_echo"],
                    skip_rpe=True,
                ))
                applied_any = True

        if applied_any:
            logger.info("Applied residue for %s: %s", user_id, residue)
        return applied_any

    def clear_user(self, user_id: str) -> None:
        with self._lock:
            self._state.pop(user_id, None)
        self._save()

    # ─── Persistence ───

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            for uid, raw in data.items():
                self._state[uid] = UserResidue(
                    user_id=uid,
                    residue=raw.get("residue", {}),
                    last_event_ts=float(raw.get("last_event_ts", 0.0)),
                    last_session_summary=raw.get("last_session_summary", ""),
                )
            logger.info("Residue loaded: %d users tracked", len(self._state))
        except Exception as exc:
            logger.warning("Residue load failed: %s", exc)

    def _save(self) -> None:
        with self._lock:
            payload = {uid: asdict(r) for uid, r in self._state.items()}
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Residue save failed: %s", exc)


# ── Module-level singleton ────────────────────────────────────────────────

_INSTANCE: ResidueManager | None = None
_INSTANCE_LOCK = threading.Lock()


def get_residue() -> ResidueManager:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = ResidueManager()
    return _INSTANCE
