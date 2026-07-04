"""
Proactive scheduler — when Koroki initiates action on her own.

Per atlas §7.2. This is the architectural turn from "talks when spoken to" to
"has internal drive to act." Captain-in-cabin: the LLM is consulted to FORM the
action, but the DECISION TO ACT comes from this subsystem reading body/world
state, not from a cron timer.

The scheduler runs as a background tick (driven by anything that calls
maybe_act() — typically the orchestrator's tick loop or the chat handler post-
response). On each tick, it evaluates several drive scores and decides whether
to fire an "initiate" event.

Drive scores (all in [0, 1]):
  - boredom: low dopamine + idle time
  - restlessness: cortisol + low engagement + awake
  - care: oxytocin spike + sustained owner absence
  - memory_cue: recently recalled memory of someone (not used Phase 3 MVP)

Each drive accumulates over time when its conditions hold and discharges when
an action fires. A weighted sum > THRESHOLD → "initiate" event emitted, with
metadata describing which drive won.

Anti-spam: minimum COOLDOWN_SECONDS between fired actions (default 30 min).
Per-drive-type cooldowns prevent the same drive from firing repeatedly.

What "initiate" means downstream:
  - Phase 3 MVP: emits an event that chat.py / discord_bot.py can listen for.
    It DOESN'T directly send a message — it tells the system "Koroki wants to
    speak to user X right now, here's why." The actual message is generated
    by the captain LLM via the normal pipeline with this trigger as context.
  - Phase 4+ might bypass the LLM for some action types (e.g. "set lighting
    dimmer") and just execute directly.

═══════════════════════════════════════════════════════════════════════════════
🐛 PREDICTED BUGS:

S1. "Koroki messages the user every 5 minutes — spammy."
    Look at: COOLDOWN_SECONDS + per-drive cooldowns. Default 30min global +
    drive-type cooldowns should prevent this. If still spammy, raise.

S2. "Koroki never initiates, even after hours of silence."
    Look at: drive_threshold + drive accumulation rates. If thresholds are
    too high or accumulation too slow, scheduler stays silent. Add debug log
    for drive scores per tick.

S3. "Koroki messages from inside a sleep state."
    Look at: maybe_act() — should short-circuit when sleep state is ASLEEP
    or FALLING_ASLEEP. Sleep is the precondition.

S4. "After restart, all drives reset and no initiation for hours."
    Look at: persistence. Drive accumulation state should save and load.
    Otherwise long absences get re-zeroed.

S5. "Owner-care initiation fires for non-owner users."
    Look at: care drive computation — should only count owner absence.
    Other-user absence triggers a different drive (loneliness, future).
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import enum
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

logger = logging.getLogger("orchestrator.meta.scheduler")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_PATH = _REPO_ROOT / "data" / "meta" / "scheduler_state.json"

# ── Tuning constants ──────────────────────────────────────────────────────
DRIVE_THRESHOLD = 0.7              # weighted-sum gate
COOLDOWN_SECONDS = 30 * 60         # 30 min global cooldown after any action

# Per-drive cooldowns (post-firing minimum interval)
DRIVE_COOLDOWNS = {
    "boredom": 90 * 60,            # 1.5h — boredom shouldn't fire too often
    "restlessness": 60 * 60,       # 1h
    "care": 4 * 3600,              # 4h — care is precious, not frequent
}

# How quickly each drive accumulates when its conditions are met (per minute)
DRIVE_ACCUMULATION = {
    "boredom": 0.02,
    "restlessness": 0.025,
    "care": 0.015,
}

# Drive weights in the final decision (sum doesn't need to be 1)
DRIVE_WEIGHTS = {
    "boredom": 0.8,
    "restlessness": 0.9,
    "care": 1.2,                   # care is the strongest signal
}


class DriveType(str, enum.Enum):
    BOREDOM = "boredom"
    RESTLESSNESS = "restlessness"
    CARE = "care"


@dataclass
class InitiateAction:
    """Emitted when scheduler decides to act."""
    drive: str                     # which drive won
    user_id: str | None            # who to address (None = general)
    intensity: float               # 0..1, how strongly we want this
    reason: str                    # short hint for diagnostics + prompt context
    timestamp: float = field(default_factory=time.time)


@dataclass
class SchedulerState:
    drives: dict[str, float] = field(default_factory=dict)  # drive_name -> [0,1] accumulation
    last_action_ts: float = 0.0
    last_drive_fired_ts: dict[str, float] = field(default_factory=dict)
    last_tick_ts: float = 0.0


class ProactiveScheduler:
    """Decides when Koroki initiates action."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._state = SchedulerState(last_tick_ts=time.time())
        self._state_path = state_path or _STATE_PATH
        self._load()

    # ─── Main API ───

    def maybe_act(self, now_ts: float | None = None) -> InitiateAction | None:
        """Tick + decide. Returns an InitiateAction if scheduler wants to act, else None.

        Reads body state from endocrine, sleep state, presence/relationships.
        Caller is responsible for handling the action (sending message via chat).
        """
        ts = now_ts if now_ts is not None else time.time()

        # Lazy imports to avoid circular imports at module load
        try:
            from ..body.endocrine import get_endocrine
            from ..body.sleep import get_sleep, SleepState
            from ..social.presence import get_presence
            from ..social.relationship import get_relationships
        except Exception as exc:
            logger.warning("scheduler imports failed: %s", exc)
            return None

        # Hard gate: don't act when asleep
        sleep_state = get_sleep().current_state()
        if sleep_state in (SleepState.ASLEEP, SleepState.FALLING_ASLEEP):
            return None

        # Hard gate: global cooldown
        with self._lock:
            since_last = ts - self._state.last_action_ts
            if since_last < COOLDOWN_SECONDS and self._state.last_action_ts > 0:
                # Still in cooldown, but tick drives anyway so they accumulate naturally
                self._tick_drives(ts, get_endocrine(), get_presence(), get_relationships())
                return None
            dt = max(0.0, ts - self._state.last_tick_ts)
            self._state.last_tick_ts = ts

        # Compute current drive conditions
        endocrine = get_endocrine()
        presence = get_presence()
        relationships = get_relationships()
        self._tick_drives(ts, endocrine, presence, relationships, dt=dt)

        # Find which drive(s) cross threshold (weighted)
        winning_drive, score, user_id, reason = self._evaluate(ts, presence, relationships)
        if winning_drive is None or score < DRIVE_THRESHOLD:
            return None

        # Per-drive cooldown check
        with self._lock:
            last_fired = self._state.last_drive_fired_ts.get(winning_drive, 0.0)
            cooldown = DRIVE_COOLDOWNS.get(winning_drive, COOLDOWN_SECONDS)
            if ts - last_fired < cooldown:
                return None
            # Fire — record and discharge
            self._state.last_action_ts = ts
            self._state.last_drive_fired_ts[winning_drive] = ts
            self._state.drives[winning_drive] = 0.0  # discharge
        self._save()

        action = InitiateAction(
            drive=winning_drive,
            user_id=user_id,
            intensity=score,
            reason=reason,
            timestamp=ts,
        )
        logger.info("Scheduler fired: drive=%s user=%s intensity=%.2f reason=%s",
                     winning_drive, user_id, score, reason)
        return action

    # ─── Internal: tick drive accumulators ───

    def _tick_drives(self, ts: float, endocrine, presence, relationships,
                       dt: float | None = None) -> None:
        """Update drive accumulators based on current body/world state."""
        if dt is None:
            with self._lock:
                dt = max(0.0, ts - self._state.last_tick_ts)
                self._state.last_tick_ts = ts
        dt_min = dt / 60.0  # accumulation rates are per-minute

        # Read body state
        components = endocrine.components
        dopa_t = components.get("dopamine_tonic")
        dopa_p = components.get("dopamine_phasic")
        cortisol = components.get("cortisol")
        oxytocin = components.get("oxytocin")

        dopa_t_level = dopa_t.level if dopa_t else 0.4
        dopa_p_level = dopa_p.level if dopa_p else 0.0
        cort_level = cortisol.level if cortisol else 0.3
        oxy_level = oxytocin.level if oxytocin else 0.3

        # Look at how long since last user activity (idle time)
        # Find the most-recently-active user
        latest_activity_ts = 0.0
        for state in presence._state.values() if hasattr(presence, '_state') else []:
            latest_activity_ts = max(latest_activity_ts, state.last_seen_ts)
        idle_seconds = ts - latest_activity_ts if latest_activity_ts > 0 else 0.0
        idle_minutes = idle_seconds / 60.0

        with self._lock:
            # Boredom: low dopamine_tonic + idle > 20min
            if dopa_t_level < 0.35 and idle_minutes > 20:
                self._state.drives["boredom"] = min(
                    1.0,
                    self._state.drives.get("boredom", 0.0) + DRIVE_ACCUMULATION["boredom"] * dt_min,
                )

            # Restlessness: cortisol elevated + idle but recently active body (NE moderate)
            ne = components.get("norepinephrine")
            ne_level = ne.level if ne else 0.3
            if cort_level > 0.5 and ne_level > 0.4 and idle_minutes > 10:
                self._state.drives["restlessness"] = min(
                    1.0,
                    self._state.drives.get("restlessness", 0.0)
                    + DRIVE_ACCUMULATION["restlessness"] * dt_min,
                )

            # Care: owner absent for hours + oxytocin still elevated (missing them)
            owner_last_seen = 0.0
            for uid, state in (presence._state or {}).items():
                if getattr(state, "is_owner", False):
                    owner_last_seen = max(owner_last_seen, state.last_seen_ts)
            owner_idle_minutes = (ts - owner_last_seen) / 60.0 if owner_last_seen > 0 else 9999
            if owner_idle_minutes > 60 and oxy_level > 0.35:
                self._state.drives["care"] = min(
                    1.0,
                    self._state.drives.get("care", 0.0) + DRIVE_ACCUMULATION["care"] * dt_min,
                )

    def _evaluate(self, ts: float, presence, relationships) -> tuple[str | None, float, str | None, str]:
        """Find the highest-weighted drive that's crossed accumulation threshold.

        Returns (drive_name, weighted_score, target_user_id, reason).
        """
        best_drive = None
        best_score = 0.0
        best_user_id = None
        best_reason = ""
        with self._lock:
            for drive_name, accum in self._state.drives.items():
                weight = DRIVE_WEIGHTS.get(drive_name, 1.0)
                score = accum * weight
                if score > best_score:
                    best_score = score
                    best_drive = drive_name

        # Pick the user this initiate is "for"
        if best_drive == "care":
            # Find owner
            for uid, state in (presence._state or {}).items():
                if getattr(state, "is_owner", False):
                    best_user_id = uid
                    best_reason = "wanting to check on owner after absence"
                    break
        elif best_drive == "boredom":
            # Direct toward most-recent user (likely owner)
            recent_uid = None
            recent_ts = 0.0
            for uid, state in (presence._state or {}).items():
                if state.last_seen_ts > recent_ts:
                    recent_uid = uid
                    recent_ts = state.last_seen_ts
            best_user_id = recent_uid
            best_reason = "quiet, thinking, wanting some sound"
        elif best_drive == "restlessness":
            recent_uid = None
            recent_ts = 0.0
            for uid, state in (presence._state or {}).items():
                if state.last_seen_ts > recent_ts:
                    recent_uid = uid
                    recent_ts = state.last_seen_ts
            best_user_id = recent_uid
            best_reason = "restless energy, wanting to do something"

        return best_drive, best_score, best_user_id, best_reason

    # ─── Diagnostics ───

    def drive_state(self) -> dict[str, float]:
        """For debug — current accumulation per drive."""
        with self._lock:
            return dict(self._state.drives)

    # ─── Persistence ───

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._state.drives = data.get("drives", {})
            self._state.last_action_ts = float(data.get("last_action_ts", 0.0))
            self._state.last_drive_fired_ts = data.get("last_drive_fired_ts", {})
            self._state.last_tick_ts = float(data.get("last_tick_ts", time.time()))
        except Exception as exc:
            logger.warning("Scheduler load failed: %s", exc)

    def _save(self) -> None:
        with self._lock:
            payload = asdict(self._state)
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Scheduler save failed: %s", exc)


# ── Module-level singleton ────────────────────────────────────────────────

_INSTANCE: ProactiveScheduler | None = None
_INSTANCE_LOCK = threading.Lock()


def get_scheduler() -> ProactiveScheduler:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = ProactiveScheduler()
    return _INSTANCE
