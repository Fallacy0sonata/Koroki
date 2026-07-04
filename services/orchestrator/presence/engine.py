"""
Presence participation engine.

Decides whether and how Koroki should participate in a channel, given:
- Channel energy (message rate, last Koroki spoke)
- Nervous system state (energy, social_battery, restlessness)

Returns a ParticipationDecision with action tier and probability.

Action tiers:
  none      — do nothing
  reaction  — add emoji reaction to a recent message, no text
  short     — 1-2 sentences, join what's happening
  full      — real message from her internal state (current thought, current mood)
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

# ── Interest / reaction mapping ───────────────────────────────────────────────
# Maps topic signals → emoji reactions Koroki would use.
# The bot populates topic_signal by scanning recent messages for keywords.

INTEREST_REACTIONS: dict[str, list[str]] = {
    "animal":    ["🐱", "🐾", "🐰", "🦊"],
    "music":     ["🎵", "🎶", "🎸"],
    "singing":   ["🎤", "🎵"],
    "chess":     ["♟️", "🤔"],
    "game":      ["🎮", "👀"],
    "funny":     ["💀", "😭", "😂"],
    "food":      ["👀", "🤤"],
    "night":     ["🌙", "👀"],
    "philosophy": ["🤔", "💭"],
    "agree":     ["👀", "✨"],
    "cute":      ["🥺", "👀"],
}

# Default reactions when nothing specific matches but she wants to react
_DEFAULT_REACTIONS = ["👀", "✨", "💭"]

# ── Energy tier thresholds ────────────────────────────────────────────────────

def _energy_tier(msg_per_min_5: float) -> str:
    if msg_per_min_5 == 0:
        return "dead"
    elif msg_per_min_5 < 0.5:
        return "quiet"
    elif msg_per_min_5 < 3.0:
        return "active"
    elif msg_per_min_5 < 8.0:
        return "chatty"
    else:
        return "busy"


_ENERGY_MULTIPLIERS = {
    "dead":   0.02,
    "quiet":  0.08,
    "active": 0.25,
    "chatty": 0.45,
    "busy":   0.30,
}


@dataclass
class ChannelEnergy:
    channel_id: int
    msg_per_min_5: float         # message rate over last 5 minutes
    msg_per_min_30: float        # message rate over last 30 minutes
    last_koroki_spoke: float     # epoch seconds (0 = never)
    mention_koroki: bool = False
    topic_signal: str = ""       # top matched topic from recent messages (optional)


@dataclass
class ParticipationDecision:
    action: str          # "none" | "reaction" | "short" | "full"
    probability: float   # final P(participate)
    reaction_emoji: str  # set if action == "reaction"
    reason: str          # debug string


def evaluate_participation(
    channel: ChannelEnergy,
    ns_state: dict[str, float],
    base_rate: float = 0.08,
) -> ParticipationDecision:
    """
    Evaluate whether Koroki should participate in this channel right now.

    Returns a ParticipationDecision. The caller decides whether to act based on
    the action tier — a dice roll is already baked into this function (probability
    is evaluated internally and action="none" returned if not firing).
    """
    now = time.time()
    tier = _energy_tier(channel.msg_per_min_5)

    # Mention is near-certain participation
    if channel.mention_koroki:
        return ParticipationDecision(
            action="full", probability=0.95, reaction_emoji="", reason="mention_override"
        )

    # Dead channel — almost never
    if tier == "dead":
        if random.random() > 0.02:
            return ParticipationDecision(action="none", probability=0.02, reaction_emoji="", reason="dead_channel")

    # Cooldown: how long since she last spoke
    since_spoke = now - channel.last_koroki_spoke if channel.last_koroki_spoke > 0 else float("inf")
    if since_spoke < 300:  # < 5 min — hard cooldown
        return ParticipationDecision(action="none", probability=0.0, reaction_emoji="", reason="hard_cooldown")

    # Cooldown decay: 5–30 min range, multiplier rises from 0.1 → 1.0
    cooldown_multiplier = min(1.0, max(0.1, (since_spoke - 300) / 1500))

    # Nervous system multipliers
    energy = ns_state.get("energy", 0.7)
    social_battery = ns_state.get("social_battery", 0.8)
    restlessness = ns_state.get("restlessness", 0.2)
    openness = ns_state.get("openness", 0.65)

    # Low social battery or low energy suppresses participation
    ns_multiplier = (energy * 0.35 + social_battery * 0.40 + openness * 0.25)
    # Restlessness adds a small boost (she wants to do something)
    restless_boost = restlessness * 0.12

    energy_multiplier = _ENERGY_MULTIPLIERS[tier]

    p = (base_rate * energy_multiplier * cooldown_multiplier * ns_multiplier + restless_boost)
    p = min(0.9, max(0.0, p))

    # Annoyingness parameter: ~6% of time she fires even when energy is low
    if random.random() < 0.06:
        p = max(p, 0.15)
        reason_suffix = " (annoyingness_spark)"
    else:
        reason_suffix = ""

    # Roll the dice
    if random.random() > p:
        return ParticipationDecision(
            action="none", probability=p, reaction_emoji="",
            reason=f"p={p:.3f}_not_fired{reason_suffix}"
        )

    # Decide action tier based on energy tier + NS state
    reaction_emoji = _pick_reaction(channel.topic_signal)

    # Low probability range → reaction only
    if p < 0.05 or tier in ("dead", "quiet"):
        return ParticipationDecision(
            action="reaction", probability=p, reaction_emoji=reaction_emoji,
            reason=f"low_p_reaction tier={tier}{reason_suffix}"
        )

    # Medium → short join
    if p < 0.15 or tier == "active":
        # 60% chance reaction, 40% short
        if random.random() < 0.4:
            return ParticipationDecision(
                action="short", probability=p, reaction_emoji="",
                reason=f"short_join tier={tier}{reason_suffix}"
            )
        return ParticipationDecision(
            action="reaction", probability=p, reaction_emoji=reaction_emoji,
            reason=f"reaction tier={tier}{reason_suffix}"
        )

    # High → full message (chatty/busy tiers with high NS state)
    return ParticipationDecision(
        action="full", probability=p, reaction_emoji="",
        reason=f"full_message tier={tier} p={p:.3f}{reason_suffix}"
    )


def _pick_reaction(topic_signal: str) -> str:
    """Pick an emoji reaction appropriate to the topic signal."""
    if topic_signal and topic_signal in INTEREST_REACTIONS:
        return random.choice(INTEREST_REACTIONS[topic_signal])
    return random.choice(_DEFAULT_REACTIONS)


def classify_topic(messages: list[str]) -> str:
    """
    Simple keyword-based topic classification for recent channel messages.
    Returns the top matched topic key or empty string.
    """
    text = " ".join(messages).lower()
    scores: dict[str, int] = {}

    _KEYWORDS: dict[str, list[str]] = {
        "animal":     ["cat", "dog", "bird", "animal", "pet", "kitten", "puppy", "fish", "bunny", "rabbit", "fox"],
        "music":      ["song", "music", "track", "album", "listen", "playlist", "band", "artist"],
        "singing":    ["sing", "sang", "singing", "voice", "vocal"],
        "chess":      ["chess", "checkmate", "pawn", "rook", "bishop", "queen", "king", "knight"],
        "game":       ["game", "gaming", "play", "player", "minecraft", "stream"],
        "funny":      ["lmao", "lol", "haha", "funny", "hilarious", "joke", "meme"],
        "food":       ["food", "eat", "eating", "hungry", "meal", "cook", "recipe", "tasty"],
        "night":      ["night", "late", "sleep", "insomnia", "dark", "stars", "moon"],
        "philosophy": ["philosophy", "meaning", "existence", "conscious", "life", "death", "why", "truth"],
    }

    for topic, keywords in _KEYWORDS.items():
        count = sum(text.count(kw) for kw in keywords)
        if count > 0:
            scores[topic] = count

    if not scores:
        return ""
    return max(scores, key=lambda k: scores[k])
