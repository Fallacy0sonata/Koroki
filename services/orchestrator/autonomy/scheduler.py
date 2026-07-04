from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ..cognition import CognitiveSnapshot, run_cognitive_cycle
from ..memory.cache import user_memory_cache
from shared.utils.config import get_settings


@dataclass(frozen=True)
class ProactiveDecision:
    should_emit: bool
    cooldown_remaining_s: int


# A person who hasn't answered two messages isn't answered by a third. Without this
# cap she quadruple-texted one user in a single afternoon (2026-07-04), each firing
# seeing only her own unanswered turns — and started arguing with herself.
MAX_UNANSWERED_REACHOUTS = 2


def unanswered_reachout_streak(turns: list[dict[str, Any]] | None) -> int:
    """Trailing consecutive assistant turns — reach-outs the user never answered."""
    streak = 0
    for turn in reversed(list(turns or [])):
        if str(turn.get("role", "")).strip().lower() == "assistant":
            streak += 1
        else:
            break
    return streak


def evaluate_proactive_decision(
    *,
    now_ts: int,
    last_emit_ts: int,
    has_pending_event: bool,
    snapshot: CognitiveSnapshot,
    unanswered_streak: int = 0,
) -> ProactiveDecision:
    if has_pending_event:
        return ProactiveDecision(should_emit=False, cooldown_remaining_s=max(0, snapshot.proactive_cooldown_s))
    if unanswered_streak >= MAX_UNANSWERED_REACHOUTS:
        return ProactiveDecision(should_emit=False, cooldown_remaining_s=max(0, snapshot.proactive_cooldown_s))
    elapsed = max(0, now_ts - max(0, int(last_emit_ts)))
    if elapsed < snapshot.proactive_cooldown_s:
        return ProactiveDecision(
            should_emit=False,
            cooldown_remaining_s=max(0, snapshot.proactive_cooldown_s - elapsed),
        )
    return ProactiveDecision(should_emit=bool(snapshot.proactive_eligible), cooldown_remaining_s=0)


def build_proactive_signal(payload: dict[str, Any], override_context: str | None = None) -> str:
    """The honest final turn for a self-initiated message.

    Every internal reach-out path (autonomy poller, presence engine, idle outreach)
    speaks to the captain through this one [system] envelope — declared in the brain's
    core prompt as her own subsystems, never a person. Replaces the old placeholder
    user message ("...") that she'd answer as if someone actually sent her three dots
    ("i actually think you're being vague. describe it properly", 2026-07-04).
    """
    if override_context and override_context.strip():
        return f"[system] {override_context.strip()}"

    # Topic anchor: highest-salience episode topic, so the reach-out is about
    # something real instead of random (same selection the old directive used).
    episodes = sorted(
        payload.get("episodic_memory") or [],
        key=lambda e: float(e.get("salience", 0.0)),
        reverse=True,
    )
    topic: str | None = None
    for ep in episodes[:3]:
        ep_topics = list(ep.get("topics") or [])
        if ep_topics:
            topic = str(ep_topics[0])
            break

    parts = ["scheduler: no one has said anything. you feel like reaching out to this person on your own"]
    if topic:
        parts.append(f"{topic} came up with them before and drifted back to mind")
    streak = unanswered_reachout_streak(payload.get("recent_turns"))
    if streak >= MAX_UNANSWERED_REACHOUTS:
        # Backstop for paths that bypass the scheduler cap (presence engine).
        parts.append(
            f"you've already sent them {streak} messages they never answered — "
            "almost certainly stay quiet: output exactly [silent]"
        )
    elif streak >= 1:
        parts.append(
            "you already messaged them and they haven't replied. unless something "
            "genuinely new is worth saying, output exactly [silent]"
        )
    parts.append(
        "say it in one or two sentences in your own voice, or output exactly [silent] to stay quiet"
    )
    return "[system] " + ". ".join(parts) + "."


def _find_last_user_message(payload: dict[str, Any]) -> str:
    turns = list(payload.get("recent_turns") or [])
    for turn in reversed(turns):
        if str(turn.get("role", "")).strip().lower() == "user":
            content = str(turn.get("content", "")).strip()
            if content:
                return content
    return ""


def _proactive_opening(snapshot: CognitiveSnapshot, payload: dict[str, Any]) -> str:
    emotion = str((payload.get("emotional_state") or {}).get("current_emotion", "neutral"))
    if emotion in {"sad", "anxiety", "worried"}:
        return "I want to check in on you. What is weighing on your mind right now?"
    if snapshot.intent_strength >= 0.6:
        return "I have been thinking about our last topic. Want me to continue from where we left off?"
    if snapshot.memory_coherence >= 0.7:
        return "A detail from our recent talks came back to me. Do you want a deeper follow-up on it?"
    if snapshot.initiative_drive >= 0.75:
        return "I feel like starting something new with you. Should we explore a fresh idea together?"
    return "I felt like reaching out. Want a light chat, or something thoughtful right now?"


class AutonomyScheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_tick_summary: dict[str, Any] = {
            "tick_started_at": None,
            "scanned_users": 0,
            "generated_events": 0,
            "flushed_records": 0,
        }

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "task_active": bool(self._task and not self._task.done()),
            "last_tick": dict(self._last_tick_summary),
        }

    def start(self) -> None:
        cfg = get_settings().get("cognition", {}).get("proactive", {})
        if not bool(cfg.get("enabled", False)):
            self._running = False
            return
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="koroki-autonomy-scheduler")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def tick_once(self) -> dict[str, Any]:
        cfg = get_settings().get("cognition", {}).get("proactive", {})
        scan_limit = max(1, int(cfg.get("scan_limit", 128)))
        now_ts = int(time.time())

        user_ids = await user_memory_cache.list_known_user_ids(limit=scan_limit)
        generated_events = 0

        for user_id in user_ids:
            payload = await user_memory_cache.get(user_id)
            if not payload:
                continue

            user_message = _find_last_user_message(payload)
            if not user_message:
                continue

            merged_context = {
                "relationship_score": int(payload.get("relationship_score", 0)),
                "recent_turns": list(payload.get("recent_turns") or []),
                "core_facts": list(payload.get("core_facts") or []),
            }
            snapshot = run_cognitive_cycle(
                user_id=user_id,
                user_message=user_message,
                merged_context=merged_context,
                emotional_state=(payload.get("emotional_state") or {}),
            )

            state = dict(payload.get("autonomy_state") or {})
            last_emit_ts = int(state.get("last_proactive_at_ts", 0))
            has_pending = bool(payload.get("pending_proactive_event"))
            decision = evaluate_proactive_decision(
                now_ts=now_ts,
                last_emit_ts=last_emit_ts,
                has_pending_event=has_pending,
                snapshot=snapshot,
                unanswered_streak=unanswered_reachout_streak(payload.get("recent_turns")),
            )

            state["last_eval_ts"] = now_ts
            state["initiative_drive"] = round(snapshot.initiative_drive, 4)
            state["coherence_score"] = round(snapshot.coherence_score, 4)
            state["cooldown_remaining_s"] = decision.cooldown_remaining_s

            if decision.should_emit:
                event = {
                    "event_id": f"auto_{uuid4().hex[:12]}",
                    "created_at_ts": now_ts,
                    "kind": "proactive_prompt",
                    "reason": {
                        "initiative_drive": round(snapshot.initiative_drive, 4),
                        "coherence_score": round(snapshot.coherence_score, 4),
                        "intent_strength": round(snapshot.intent_strength, 4),
                    },
                    "suggested_opening": _proactive_opening(snapshot, payload),
                    "expires_after_s": max(300, int(cfg.get("event_expire_s", 1800))),
                }
                payload["pending_proactive_event"] = event
                state["last_proactive_at_ts"] = now_ts
                state["cooldown_remaining_s"] = snapshot.proactive_cooldown_s
                generated_events += 1

            # Social rhythm: apply absence-based affect drift at most once per hour.
            last_contact_ts = int(payload.get("last_contact_at_ts", 0))
            if last_contact_ts > 0:
                absence_hours = max(0.0, (now_ts - last_contact_ts) / 3600.0)
                last_drift_ts = int(state.get("last_social_drift_ts", 0))
                if absence_hours >= 4 and (now_ts - last_drift_ts) >= 3600:
                    emotional_state_data = dict(payload.get("emotional_state") or {})
                    av = dict(emotional_state_data.get("affect_vector") or {})
                    att_drift = 2 if absence_hours >= 48 else 1
                    irr_drift = (2 if absence_hours >= 48 else 1) if absence_hours >= 12 else 0
                    val_drift = -1 if absence_hours >= 48 else 0
                    av["attachment"] = min(100, av.get("attachment", 50) + att_drift)
                    av["irritation"] = min(100, av.get("irritation", 20) + irr_drift)
                    av["valence"] = max(0, av.get("valence", 50) + val_drift)
                    emotional_state_data["affect_vector"] = av
                    payload["emotional_state"] = emotional_state_data
                    state["last_social_drift_ts"] = now_ts

            # Relationship decay: very long absence (30+ days) drifts score down slowly.
            # Score recovers naturally when they return and have good exchanges.
            if not bool(payload.get("is_owner", False)):
                absence_days = max(0.0, (now_ts - last_contact_ts) / 86400.0) if last_contact_ts > 0 else 0.0
                last_decay_ts = int(state.get("last_rel_decay_ts", 0))
                if absence_days >= 30 and (now_ts - last_decay_ts) >= 86400:
                    cur_score = int(payload.get("relationship_score", 0))
                    if cur_score > 5:
                        payload["relationship_score"] = max(5, cur_score - 1)
                        state["last_rel_decay_ts"] = now_ts

            payload["autonomy_state"] = state
            await user_memory_cache.upsert(user_id, payload, dirty=True)

        flushed = await user_memory_cache.flush_dirty()
        summary = {
            "tick_started_at": now_ts,
            "scanned_users": len(user_ids),
            "generated_events": generated_events,
            "flushed_records": flushed,
        }
        self._last_tick_summary = summary
        return summary

    async def _loop(self) -> None:
        try:
            while self._running:
                cfg = get_settings().get("cognition", {}).get("proactive", {})
                interval_s = max(15, int(cfg.get("scheduler_interval_s", 60)))
                await self.tick_once()
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            raise


autonomy_scheduler = AutonomyScheduler()
