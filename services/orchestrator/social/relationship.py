"""
Relationships — per-user state with trust accumulation.

Per atlas §6.1 + §6.3. Distinct from `presence.py`:
  - presence: "are they here right now" (session-level, ephemeral)
  - relationship: "who are they to me" (cross-session, persistent)

State per user:
  - score: continuous relationship score [0, 100] (the existing "relationship score" concept)
  - trust: continuous trust level [0, 1] — slow to build, faster to lose
  - is_owner: bool (locked at user creation, not derived)
  - first_seen_ts, last_seen_ts, total_interactions
  - sustained_seen_minutes: cumulative time-in-presence with this user

Trust dynamics (per §6.3):
  - Small positive interactions add slowly (+0.005 per warm message)
  - Conflict / negative events subtract faster (-0.05 per conflict)
  - Sustained presence adds trust slowly (+0.002 per 5min co-presence)
  - Long absence with no contact erodes trust slightly (-0.001/day after 7d)

Relationship score dynamics (preserves existing system):
  - Bands match existing engine: <15 stranger, 15-39 acquainted, 40-69 known, ≥70 close, owner=unlocked
  - Updates from chat events same as existing scoring

Endocrine coupling:
  - High trust on incoming message → oxytocin baseline elevated for that interaction
  - Distrust → cortisol baseline elevated when that user shows up

═══════════════════════════════════════════════════════════════════════════════
🐛 PREDICTED BUGS:

R1. "Trust never goes up despite many positive interactions."
   Look at: TRUST_GAIN_PER_WARM constant + how/when it's called. Trust gains
   are intentionally tiny — accumulates over weeks, not hours. If you expect
   visible movement in a day's chat, that's not how it should work.

R2. "Trust crashes to zero on a single bad interaction."
   Look at: TRUST_LOSS_ON_CONFLICT — should be ~0.05, not 1.0. Trust is
   asymmetric (slower to build, faster to lose) but not catastrophic.

R3. "Owner is treated as a stranger after restart."
   Look at: is_owner field persistence in relationship JSON. Should be set
   once at creation and never recomputed.

R4. "Score and trust never persist."
   Look at: data/social/relationships.json. Should save on update + load on
   init. If file missing, may indicate save() isn't being called.

R5. "Long absence forgives everything — old conflicts fully forgotten."
   Look at: absence decay only erodes trust slightly per week. We don't have
   a forgiveness mechanism. Conflicts stick unless balanced by positive
   interactions. This is intentional.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

logger = logging.getLogger("orchestrator.social.relationship")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_PATH = _REPO_ROOT / "data" / "social" / "relationships.json"

# ── Tuning constants ──────────────────────────────────────────────────────
TRUST_GAIN_PER_WARM = 0.005       # tiny per-interaction gain
TRUST_GAIN_PER_SUSTAINED_MIN = 0.0004  # per minute of co-presence
TRUST_LOSS_ON_CONFLICT = 0.05     # 10x faster to lose than gain
TRUST_DECAY_PER_DAY_AFTER_ABSENCE = 0.001  # very slow
ABSENCE_DECAY_AFTER_DAYS = 7      # only starts after 7 days no contact

OWNER_TRUST_FLOOR = 0.85          # owner can't drop below this trust
NORMAL_TRUST_CEILING = 0.95


@dataclass
class UserRelationship:
    user_id: str
    is_owner: bool = False
    score: float = 0.0                    # 0..100 continuous band score
    trust: float = 0.5                    # 0..1
    first_seen_ts: float = 0.0
    last_seen_ts: float = 0.0
    total_interactions: int = 0
    sustained_seen_minutes: float = 0.0
    last_conflict_ts: float = 0.0
    metadata: dict = field(default_factory=dict)


class RelationshipManager:
    """Single-instance, threadsafe per-user relationship state."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, UserRelationship] = {}
        self._state_path = state_path or _STATE_PATH
        self._load()

    # ─── Read API ───

    def get(self, user_id: str) -> UserRelationship | None:
        with self._lock:
            return self._state.get(user_id)

    def get_or_create(self, user_id: str, is_owner: bool = False) -> UserRelationship:
        with self._lock:
            r = self._state.get(user_id)
            if r is None:
                ts = time.time()
                r = UserRelationship(
                    user_id=user_id,
                    is_owner=is_owner,
                    trust=OWNER_TRUST_FLOOR if is_owner else 0.5,
                    first_seen_ts=ts,
                    last_seen_ts=ts,
                )
                self._state[user_id] = r
                logger.info("New relationship: user=%s owner=%s", user_id, is_owner)
            return r

    def trust(self, user_id: str) -> float:
        r = self.get(user_id)
        return r.trust if r else 0.5

    # ─── Event handlers ───

    def note_warm_interaction(self, user_id: str, is_owner: bool = False) -> None:
        r = self.get_or_create(user_id, is_owner)
        with self._lock:
            ceiling = NORMAL_TRUST_CEILING
            r.trust = min(ceiling, r.trust + TRUST_GAIN_PER_WARM)
            r.last_seen_ts = time.time()
            r.total_interactions += 1
        self._save()

    def note_neutral_interaction(self, user_id: str, is_owner: bool = False) -> None:
        r = self.get_or_create(user_id, is_owner)
        with self._lock:
            r.last_seen_ts = time.time()
            r.total_interactions += 1
        self._save()

    def note_conflict(self, user_id: str, is_owner: bool = False) -> None:
        r = self.get_or_create(user_id, is_owner)
        with self._lock:
            floor = OWNER_TRUST_FLOOR if r.is_owner else 0.0
            r.trust = max(floor, r.trust - TRUST_LOSS_ON_CONFLICT)
            r.last_seen_ts = time.time()
            r.last_conflict_ts = time.time()
            r.total_interactions += 1
        logger.info("Conflict noted for %s: trust now %.3f", user_id, r.trust)
        self._save()

    def note_sustained_presence(self, user_id: str, minutes: float,
                                  is_owner: bool = False) -> None:
        r = self.get_or_create(user_id, is_owner)
        with self._lock:
            ceiling = NORMAL_TRUST_CEILING
            r.trust = min(ceiling, r.trust + TRUST_GAIN_PER_SUSTAINED_MIN * minutes)
            r.sustained_seen_minutes += minutes
            r.last_seen_ts = time.time()
        self._save()

    def update_score(self, user_id: str, new_score: float,
                     is_owner: bool = False) -> None:
        """Set band score from existing engine. Doesn't affect trust directly."""
        r = self.get_or_create(user_id, is_owner)
        with self._lock:
            r.score = max(0.0, min(100.0, new_score))
        self._save()

    # ─── Maintenance tick ───

    def tick_absence_decay(self, now_ts: float | None = None) -> None:
        """Slowly erode trust on users not seen in > 7 days. Non-owners only."""
        ts = now_ts if now_ts is not None else time.time()
        changed = False
        with self._lock:
            for r in self._state.values():
                if r.is_owner:
                    continue
                days_absent = (ts - r.last_seen_ts) / 86400.0
                if days_absent > ABSENCE_DECAY_AFTER_DAYS:
                    days_past_grace = days_absent - ABSENCE_DECAY_AFTER_DAYS
                    decay = TRUST_DECAY_PER_DAY_AFTER_ABSENCE * days_past_grace
                    new_trust = max(0.0, r.trust - decay)
                    if new_trust != r.trust:
                        r.trust = new_trust
                        changed = True
        if changed:
            self._save()

    # ─── Persistence ───

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            for uid, raw in data.items():
                self._state[uid] = UserRelationship(
                    user_id=uid,
                    is_owner=raw.get("is_owner", False),
                    score=float(raw.get("score", 0.0)),
                    trust=float(raw.get("trust", 0.5)),
                    first_seen_ts=float(raw.get("first_seen_ts", 0.0)),
                    last_seen_ts=float(raw.get("last_seen_ts", 0.0)),
                    total_interactions=int(raw.get("total_interactions", 0)),
                    sustained_seen_minutes=float(raw.get("sustained_seen_minutes", 0.0)),
                    last_conflict_ts=float(raw.get("last_conflict_ts", 0.0)),
                    metadata=raw.get("metadata", {}),
                )
            logger.info("Relationships loaded: %d users tracked", len(self._state))
        except Exception as exc:
            logger.warning("Relationships load failed: %s", exc)

    def _save(self) -> None:
        with self._lock:
            payload = {uid: asdict(r) for uid, r in self._state.items()}
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Relationships save failed: %s", exc)


# ── Module-level singleton ────────────────────────────────────────────────

_INSTANCE: RelationshipManager | None = None
_INSTANCE_LOCK = threading.Lock()


def get_relationships() -> RelationshipManager:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = RelationshipManager()
    return _INSTANCE
