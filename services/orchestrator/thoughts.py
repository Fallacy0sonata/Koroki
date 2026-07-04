"""
Koroki's persistent current thought state.

Primary source: data/thoughts/history.json (generated hourly by thought_generator.py).
Fallback: static pool below, selected by hour, when the file is missing or stale.

get_current_thought() is called on every chat request and injected as a core_fact
so the model always knows what it's been thinking about.
get_recent_thoughts() returns the last N entries for potential "what have you been
thinking about?" context.
"""

from __future__ import annotations

import hashlib
import time

from .thought_generator import load_history

# Static fallback pool — used only during the brief startup window (~15s) before
# the first generated thought arrives. Not needed after that.
_FALLBACK_POOL = [
    "rereading a line from a book she put down weeks ago and finally getting it",
    "trying to remember the name of a song she heard once and can't place",
    "noticing it's gotten dark outside without realizing how long she'd been sitting",
    "halfway through a thought she started earlier and didn't finish",
    "something a person said to her that she keeps turning over in her head",
    "wondering if she'd make the same choice again if she had to",
    "thinking about an animal she saw earlier and whether it was okay",
    "the specific feeling of a room right before it rains",
    "quietly annoyed at herself for something she said wrong",
    "a word she likes the sound of and keeps mentally saying to herself",
    "whether she actually wants what she thinks she wants",
    "the kind of tired that sleep doesn't quite fix",
    "an argument she's already won in her head three times",
    "noticing that the air smells different tonight and not knowing why",
    "something she started that she hasn't gone back to finish",
    "a scene from something she watched that stuck with her longer than expected",
    "deciding whether a certain person deserves more patience than she's been giving them",
    "what it would feel like to just leave everything and go somewhere no one knows her",
    "trying to figure out if she's bored or if something is actually wrong",
    "a detail she noticed about someone that she can't stop thinking about",
    "whether the version of herself from a few years ago would recognize her now",
    "the exact texture of silence in an empty house",
    "a question she knows the answer to but keeps second-guessing",
    "an idea she had that felt important but she never wrote down",
]

def _fallback_thought() -> str:
    idx = int(hashlib.md5(str(int(time.time()) // 3600).encode()).hexdigest(), 16) % len(_FALLBACK_POOL)
    return _FALLBACK_POOL[idx]


def get_current_thought() -> str:
    """Return Koroki's current thought. Generated hourly; falls back to static pool on first startup."""
    history = load_history()
    if history:
        return history[-1]["thought"]
    return _fallback_thought()


def get_recent_thoughts(n: int = 7) -> list[dict]:
    """Return the last n thought entries with timestamps, most recent last."""
    history = load_history()
    return history[-n:] if history else []
