"""
Topic interest weights for Koroki.

Defines what Koroki genuinely cares about and how strongly she reacts when those
topics come up. Prevents fake enthusiasm for low-weight topics and ensures genuine
engagement (follow-up questions, real reaction) for high-weight ones.

build_topic_fact() is the public API — call it on the user's message, inject the
returned string as a core_fact. prompt_builder translates it into natural guidance.

Weight scale:
    0-15   → nothing notable, no special handling
    16-40  → light interest — mild engagement, no forced follow-up
    41-70  → engaged — genuine interest, follow-up natural if conversation allows
    71-100 → strong — clear reaction, she'd actually ask about it

Valence:
    positive → she engages warmly / curiously
    negative → something concerns or troubles her — react, not ignore
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class InterestCategory:
    name: str
    weight: int
    valence: str           # "positive" | "negative"
    keywords: tuple[str, ...]


# Koroki's interests and aversions. First match with highest weight wins.
_INTERESTS: list[InterestCategory] = [
    # ── Strong positive interests (71-100) ──
    InterestCategory(
        "music", 90, "positive",
        ("music", "song", "songs", "singer", "singing", "melody", "album", "playlist",
         "yoasobi", "ado", "lisa", "jpop", "j-pop", "j pop", "band", "track", "tracks",
         "listen to", "listening to", "concert", "lyrics", "genre", "mv", "music video"),
    ),
    InterestCategory(
        "singing_voice", 85, "positive",
        ("sing", "sang", "sung", "voice", "vocals", "vocal", "karaoke", "pitch",
         "humming", "hum", "acapella", "cover song", "covered a song"),
    ),
    InterestCategory(
        "philosophy", 78, "positive",
        ("meaning of", "purpose of", "existence", "consciousness", "reality", "truth",
         "what is life", "why are we", "why do people", "does it matter",
         "philosophical", "free will", "what makes us human", "question everything"),
    ),
    InterestCategory(
        "night_quiet", 72, "positive",
        ("late night", "midnight", "3am", "2am", "1am", "4am", "can't sleep", "insomnia",
         "silence", "quiet night", "alone at night", "night sky", "staring at the ceiling",
         "lying awake"),
    ),

    # ── Medium-high positive (41-70) ──
    InterestCategory(
        "rain_atmosphere", 68, "positive",
        ("rain", "raining", "rainy", "drizzle", "thunder", "lightning",
         "cloudy", "overcast", "fog", "misty", "stormy outside"),
    ),
    InterestCategory(
        "aesthetics", 65, "positive",
        ("purple", "aesthetic", "soft lighting", "ambiance", "atmosphere",
         "vibe", "mood lighting", "cozy", "elegant", "beautiful space", "interior"),
    ),
    InterestCategory(
        "animals", 62, "positive",
        ("cat", "cats", "kitten", "kittens", "kitty", "dog", "puppy",
         "animal", "animals", "bird", "birds", "bunny", "rabbit",
         "stray cat", "adopted a", "rescued a"),
    ),
    InterestCategory(
        "food_she_likes", 58, "positive",
        ("steak", "wagyu", "sushi", "ramen", "noodles", "matcha", "green tea",
         "hot pot", "hotpot", "nabe", "tempura", "sashimi", "tonkatsu",
         "good food", "amazing food", "best meal"),
    ),
    InterestCategory(
        "art", 55, "positive",
        ("art", "painting", "drawing", "illustration", "artwork", "artist",
         "sketch", "photography", "design", "visual art", "gallery", "museum"),
    ),
    InterestCategory(
        "books", 52, "positive",
        ("book", "books", "reading", "novel", "fiction", "story", "stories",
         "author", "chapter", "literature", "manga", "light novel", "read a"),
    ),

    # ── Light positive (16-40) ──
    InterestCategory(
        "games", 38, "positive",
        ("game", "gaming", "played", "video game", "rpg", "fps", "gacha",
         "match", "won a", "lost a", "ranked", "level up", "cleared"),
    ),
    InterestCategory(
        "food_general", 28, "positive",
        ("food", "eating", "ate", "meal", "lunch", "dinner", "breakfast",
         "cooked", "cooking", "restaurant", "snack", "hungry", "tasted"),
    ),

    # ── Negative valence — concern / reaction, not enthusiasm ──
    InterestCategory(
        "weapons_explosives", 82, "negative",
        ("bomb", "bombs", "explosive", "explosives", "grenade", "weapon", "weapons",
         "gun", "guns", "rifle", "shoot someone", "ammo", "ammunition",
         "missile", "landmine", "detonator"),
    ),
    InterestCategory(
        "violence", 65, "negative",
        ("beat up", "beaten up", "attacked", "assaulted", "stabbed", "stab",
         "bleeding badly", "hospitalized from", "jumped by", "mugged"),
    ),
    InterestCategory(
        "dangerous_reckless", 48, "negative",
        ("drunk driving", "overdose", "self harm", "no helmet", "no seatbelt",
         "jumped off", "dangerous stunt", "could have died"),
    ),
]


def _effective_weight(interest: InterestCategory) -> int:
    """Base weight + lived-experience drift (positive interests only — aversions are
    character constants). Falls back to the base weight if the drift layer is unavailable."""
    if interest.valence != "positive":
        return interest.weight
    try:
        from .mind.interest_drift import get_interest_drift
        return get_interest_drift().effective_weight(interest.name, interest.weight)
    except Exception:
        return interest.weight


def base_weight(name: str) -> int:
    """Hand-tuned base weight for a category (0 if unknown). Used by the drift layer."""
    for interest in _INTERESTS:
        if interest.name == name:
            return interest.weight
    return 0


def analyze_message(message: str) -> tuple[str | None, int, str]:
    """Return (topic_name, weight, valence) of the highest-weight matching interest, or (None, 0, 'neutral')."""
    lowered = re.sub(r"\s+", " ", message.lower()).strip()
    best: InterestCategory | None = None
    best_weight = 0
    for interest in _INTERESTS:
        if any(kw in lowered for kw in interest.keywords):
            w = _effective_weight(interest)
            if best is None or w > best_weight:
                best = interest
                best_weight = w
    if best is None:
        return None, 0, "neutral"
    return best.name, best_weight, best.valence


def get_reactivity_level(weight: int) -> str:
    if weight >= 71:
        return "strong"
    if weight >= 41:
        return "engaged"
    if weight >= 16:
        return "light"
    return "none"


def build_topic_fact(message: str) -> str | None:
    """Build a core_fact string for topic interest, or None if nothing notable matched."""
    topic, weight, valence = analyze_message(message)
    if topic is None or weight < 16:
        return None
    level = get_reactivity_level(weight)
    return f"topic_interest={topic};weight={weight};valence={valence};reactivity={level}"
