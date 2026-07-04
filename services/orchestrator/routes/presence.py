"""
GET  /v1/presence/channels          — dump stored channel energy data
POST /v1/presence/evaluate          — evaluate participation for a single channel
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from ..presence import ChannelEnergy, ParticipationDecision, evaluate_participation
from ..nervous_system import get_state

logger = logging.getLogger("orchestrator.presence")
router = APIRouter()

_repo_root = Path(__file__).resolve().parents[3]
_ENERGY_FILE = _repo_root / "data" / "presence" / "channel_energy.json"


class ChannelEnergyRequest(BaseModel):
    channel_id: int
    msg_per_min_5: float = 0.0
    msg_per_min_30: float = 0.0
    last_koroki_spoke: float = 0.0
    mention_koroki: bool = False
    topic_signal: str = ""
    recent_messages: list[str] = []


class ParticipationResponse(BaseModel):
    action: str
    probability: float
    reaction_emoji: str
    reason: str
    ns_snapshot: dict


@router.get("/presence/channels")
async def get_channel_energy() -> dict:
    """Return stored channel energy metrics."""
    try:
        with open(_ENERGY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


@router.post("/presence/evaluate", response_model=ParticipationResponse)
async def evaluate_channel(req: ChannelEnergyRequest) -> ParticipationResponse:
    """
    Evaluate participation for one channel.
    If recent_messages are provided, topic_signal is derived from them.
    """
    from ..presence.engine import classify_topic

    topic = req.topic_signal
    if not topic and req.recent_messages:
        topic = classify_topic(req.recent_messages)

    channel = ChannelEnergy(
        channel_id=req.channel_id,
        msg_per_min_5=req.msg_per_min_5,
        msg_per_min_30=req.msg_per_min_30,
        last_koroki_spoke=req.last_koroki_spoke,
        mention_koroki=req.mention_koroki,
        topic_signal=topic,
    )

    ns_state = get_state()
    decision = evaluate_participation(channel, ns_state)

    return ParticipationResponse(
        action=decision.action,
        probability=round(decision.probability, 4),
        reaction_emoji=decision.reaction_emoji,
        reason=decision.reason,
        ns_snapshot={k: round(v, 3) for k, v in ns_state.items()},
    )
