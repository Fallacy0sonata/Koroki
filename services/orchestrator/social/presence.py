"""
Presence tracker — per-user online state with sustained-presence event emission.

Why this matters:
  In Phase 1+2A, oxytocin only rose from individual affectionate messages.
  But real bonding builds with *time spent together*, not just from words said.
  This tracker emits `sustained_presence` events on a timer when a user has
  been actively present for a while, which the endocrine system uses to build
  oxytocin baseline.

Design (single instance, threadsafe):
  - Per-user state: last_seen_ts, session_start_ts, session_message_count,
    last_sustained_event_ts
  - "Active" if last_seen within ACTIVE_WINDOW_SECONDS (default 5 min)
  - "Session" = continuous active presence; resets when user goes inactive
  - `tick(now)` called periodically — emits sustained_presence events for users
    whose session has crossed thresholds since last emit
  - Owner gets higher event intensity and longer sustain multipliers

Integration:
  Called from chat.py per request via `note_activity(user_id, is_owner)`.
  The chat handler also calls `maybe_emit_sustained_events()` to fire any
  pending sustained_presence events into the endocrine system.

═══════════════════════════════════════════════════════════════════════════════
🐛 PREDICTED BUGS / WATCH-FOR-WHEN-LIVE-TESTING:

1. Symptom: "She gets warmer over a long session" works for one user but not
   for owner, OR vice versa.
   Look at: OWNER_INTENSITY_MULTIPLIER constant + the is_owner check in
   `maybe_emit_sustained_events`. Verify chat.py is passing is_owner=True.

2. Symptom: User sends rapid messages (every 5s) but no sustained_presence
   fires even after 10 minutes of active chat.
   Look at: SUSTAINED_THRESHOLDS list. They're absolute time-since-session-start
   gates. If session_start_ts isn't being set correctly, thresholds never trip.

3. Symptom: User goes idle for 20 min, comes back, immediately gets
   "sustained_presence" event from earlier thresholds.
   Look at: Session reset logic — when last_seen exceeds ACTIVE_WINDOW_SECONDS,
   we should reset session_start_ts AND last_sustained_event_ts. If we don't,
   the new session inherits old threshold state.

4. Symptom: Sustained_presence events fire on EVERY tick after first threshold
   is crossed (oxytocin blasts up to 1.0 and stays).
   Look at: `last_sustained_event_ts` update logic — we must update it AFTER
   emitting so we don't re-fire the same threshold. Also the threshold should
   be "crossed since last emit," not "current value above threshold."

5. Symptom: Multi-user sessions cause wrong user's presence to drive oxytocin.
   Look at: Per-user state dict — verify state is keyed by user_id and we
   emit events with the correct user_id, AND that the endocrine integration
   in chat.py only forwards owner's sustained_presence to oxytocin.

6. Symptom: After brain restart, sustained_presence behaves as if no session
   exists (no warm-up effect).
   Look at: Persistence in `data/social/presence_state.json`. State should
   load on init. If we WANT cold-start behavior on restart, document that.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("orchestrator.social.presence")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_PATH = _REPO_ROOT / "data" / "social" / "presence_state.json"

# ── Tuning constants ──────────────────────────────────────────────────────
# After this many seconds without activity, user is considered "left."
ACTIVE_WINDOW_SECONDS = 5 * 60  # 5 minutes

# Thresholds (seconds-into-session) at which sustained_presence events fire.
# Each threshold fires at most once per session. Tuned so that:
#   - 1 min: small "we've been here a bit" signal
#   - 5 min: meaningful sustained presence
#   - 20 min: sustained intimacy
#   - 60 min: deep session
SUSTAINED_THRESHOLDS = [
    (60, 0.3),       # (seconds_into_session, base_intensity)
    (5 * 60, 0.5),
    (20 * 60, 0.7),
    (60 * 60, 0.9),
]

# Owner gets ~50% stronger sustained_presence effect (more oxytocin per minute).
OWNER_INTENSITY_MULTIPLIER = 1.5


@dataclass
class UserPresence:
    user_id: str
    is_owner: bool = False
    last_seen_ts: float = 0.0
    session_start_ts: float = 0.0
    session_message_count: int = 0
    last_sustained_event_ts: float = 0.0
    # Thresholds we've already fired in this session — reset on session restart.
    fired_thresholds_seconds: list[int] = field(default_factory=list)


@dataclass
class SustainedPresenceEvent:
    """Emitted when a user crosses a sustained-presence threshold."""

    user_id: str
    is_owner: bool
    seconds_into_session: int  # which threshold tripped
    intensity: float           # adjusted by owner multiplier
    timestamp: float = field(default_factory=time.time)


class PresenceTracker:
    """Single-instance, threadsafe per-user presence state."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, UserPresence] = {}
        self._state_path = state_path or _STATE_PATH
        self._load()

    # ------------------------------------------------------------------
    # API: chat.py calls these
    # ------------------------------------------------------------------

    def note_activity(self, user_id: str, is_owner: bool = False,
                       now_ts: float | None = None) -> None:
        """Mark user as active right now. Starts/extends session."""
        ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            existing = self._state.get(user_id)
            if existing is None:
                self._state[user_id] = UserPresence(
                    user_id=user_id,
                    is_owner=is_owner,
                    last_seen_ts=ts,
                    session_start_ts=ts,
                    session_message_count=1,
                )
                logger.info("Presence: new session for %s (owner=%s)", user_id, is_owner)
                return

            # Check session continuity. If gap exceeds ACTIVE_WINDOW, new session.
            gap = ts - existing.last_seen_ts
            if gap > ACTIVE_WINDOW_SECONDS:
                # Reset session — they went away and came back
                logger.info(
                    "Presence: session reset for %s (gap=%.1fs > window=%ds)",
                    user_id, gap, ACTIVE_WINDOW_SECONDS,
                )
                existing.session_start_ts = ts
                existing.last_sustained_event_ts = 0.0
                existing.fired_thresholds_seconds = []
                existing.session_message_count = 1
            else:
                existing.session_message_count += 1

            existing.last_seen_ts = ts
            existing.is_owner = is_owner  # may have changed (rare but possible)

    def maybe_emit_sustained_events(self, now_ts: float | None = None
                                     ) -> list[SustainedPresenceEvent]:
        """Check all active users; emit sustained_presence events for newly-crossed thresholds.

        Returns a list of events. Each is emitted once per session per threshold.
        Inactive users (last_seen > ACTIVE_WINDOW ago) are skipped.
        """
        ts = now_ts if now_ts is not None else time.time()
        events: list[SustainedPresenceEvent] = []
        with self._lock:
            for user_id, presence in self._state.items():
                # Skip inactive
                if ts - presence.last_seen_ts > ACTIVE_WINDOW_SECONDS:
                    continue
                seconds_in_session = ts - presence.session_start_ts
                if seconds_in_session < SUSTAINED_THRESHOLDS[0][0]:
                    continue
                for threshold_seconds, base_intensity in SUSTAINED_THRESHOLDS:
                    if seconds_in_session < threshold_seconds:
                        break  # haven't crossed this one yet
                    if threshold_seconds in presence.fired_thresholds_seconds:
                        continue  # already fired this session
                    intensity = base_intensity
                    if presence.is_owner:
                        intensity *= OWNER_INTENSITY_MULTIPLIER
                    intensity = min(1.0, intensity)
                    events.append(SustainedPresenceEvent(
                        user_id=user_id,
                        is_owner=presence.is_owner,
                        seconds_into_session=threshold_seconds,
                        intensity=intensity,
                        timestamp=ts,
                    ))
                    presence.fired_thresholds_seconds.append(threshold_seconds)
                    presence.last_sustained_event_ts = ts
                    logger.info(
                        "Presence: sustained_presence emitted user=%s threshold=%ds intensity=%.2f",
                        user_id, threshold_seconds, intensity,
                    )
        return events

    def is_active(self, user_id: str, now_ts: float | None = None) -> bool:
        """Has this user been seen recently (within ACTIVE_WINDOW)?"""
        ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            p = self._state.get(user_id)
            if not p:
                return False
            return ts - p.last_seen_ts <= ACTIVE_WINDOW_SECONDS

    def session_seconds(self, user_id: str, now_ts: float | None = None) -> float:
        """How many seconds has this user been actively present in current session?"""
        ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            p = self._state.get(user_id)
            if not p or ts - p.last_seen_ts > ACTIVE_WINDOW_SECONDS:
                return 0.0
            return ts - p.session_start_ts

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            for user_id, raw in data.items():
                self._state[user_id] = UserPresence(
                    user_id=user_id,
                    is_owner=raw.get("is_owner", False),
                    last_seen_ts=raw.get("last_seen_ts", 0.0),
                    session_start_ts=raw.get("session_start_ts", 0.0),
                    session_message_count=raw.get("session_message_count", 0),
                    last_sustained_event_ts=raw.get("last_sustained_event_ts", 0.0),
                    fired_thresholds_seconds=list(raw.get("fired_thresholds_seconds", [])),
                )
            logger.info("Presence state loaded: %d users tracked", len(self._state))
        except Exception as exc:
            logger.warning("Presence state load failed: %s", exc)

    def save(self) -> None:
        with self._lock:
            payload = {uid: asdict(p) for uid, p in self._state.items()}
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Presence state save failed: %s", exc)


# ────────────────────────────────────────────────────────────────────
# Module-level singleton
# ────────────────────────────────────────────────────────────────────

_INSTANCE: PresenceTracker | None = None
_INSTANCE_LOCK = threading.Lock()


def get_presence() -> PresenceTracker:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = PresenceTracker()
    return _INSTANCE
