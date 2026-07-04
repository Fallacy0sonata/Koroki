"""
Song intent detection for Koroki singing pipeline.

Parses user messages to detect singing requests and extract the song name.
Patterns cover natural phrasing: "sing X", "can you sing X", "sing me X", etc.
"""

from __future__ import annotations

import re

_PATTERNS: list[re.Pattern[str]] = [
    # "sing [me/us] <song>"
    re.compile(
        r"\bsing(?:\s+(?:me|us|for\s+me|for\s+us))?\s+(.+)",
        re.IGNORECASE,
    ),
    # "can you sing <song>" / "could you sing <song>"
    re.compile(
        r"\b(?:can|could|would|will)\s+you\s+sing(?:\s+(?:me|us))?\s+(.+)",
        re.IGNORECASE,
    ),
    # "i want to hear you sing <song>" / "i want you to sing <song>"
    re.compile(
        r"\bi\s+want(?:\s+to\s+hear)?\s+you\s+to?\s+sing\s+(.+)",
        re.IGNORECASE,
    ),
    # "please sing <song>"
    re.compile(
        r"\bplease\s+sing(?:\s+(?:me|us))?\s+(.+)",
        re.IGNORECASE,
    ),
    # "do <song>" when prefixed by singing context — caught by simpler patterns above
]

# Words that, if they ARE the entire extracted song name, indicate a bare "sing!"
# with no song specified — not a real song request.
_BARE_WORDS = {"something", "a song", "anything", "for me", "for us", "now", "please"}

# Filler words to strip from the end of the extracted song name
_TRAIL_STRIP = re.compile(r"\s*[,!?.]+$")


def detect_singing_intent(message: str) -> str | None:
    """Return the song name if the message is a singing request, else None.

    Returns a cleaned song name string, or None if no singing intent found.
    """
    text = message.strip()
    # Remove Discord mention prefix if present (e.g. "<@12345> sing ...")
    text = re.sub(r"^<@!?\d+>\s*", "", text)

    for pattern in _PATTERNS:
        m = pattern.search(text)
        if m:
            song = _TRAIL_STRIP.sub("", m.group(1).strip())
            if not song or song.lower() in _BARE_WORDS:
                return None
            return song

    return None
