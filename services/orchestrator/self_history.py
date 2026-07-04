"""
Koroki's self-history document — who she is, what has happened, what changed.

Injected into context when the conversation warrants it: identity questions,
introspective topics, or when relationship is close enough that she'd open up.
Not injected on every request — that would make it feel rehearsed.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("orchestrator.self_history")

_repo_root = Path(__file__).resolve().parents[2]
_HISTORY_FILE = _repo_root / "data" / "koroki" / "self_history.md"

# Keywords that suggest a self-history injection would be relevant
_IDENTITY_SIGNALS = frozenset([
    "who are you", "what are you", "are you real", "are you conscious", "do you feel",
    "do you remember", "have you always", "what happened", "your past", "your history",
    "been here", "how long", "always been", "used to be", "changed", "you've changed",
    "you changed", "what kind of", "tell me about yourself", "yourself",
])


@lru_cache(maxsize=1)
def _load_history() -> str:
    """Load self-history file. Cached — file rarely changes."""
    try:
        return _HISTORY_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning("Self-history file not found: %s", _HISTORY_FILE)
        return ""


def _reload_history() -> str:
    """Force reload (clears cache). Call if file is updated at runtime."""
    _load_history.cache_clear()
    return _load_history()


def should_inject_history(message: str, relationship_score: int, is_owner: bool) -> bool:
    """
    Returns True if this message warrants injecting self-history context.
    Requires both a signal word AND sufficient relationship depth.
    """
    if relationship_score < 30 and not is_owner:
        return False
    msg_lower = message.lower()
    return any(signal in msg_lower for signal in _IDENTITY_SIGNALS)


def get_history_block() -> str:
    """Return the self-history content formatted as a context block.

    The static self-history is followed by her recent journal day entries — the living
    part of her autobiography (mind/journal.py). Identity questions get answered from
    what actually happened, not confabulation.
    """
    content = _load_history()
    parts = []
    if content:
        parts.append(f"## Your Self-History\n{content}")
    try:
        from .mind.journal import journal
        recent = journal().recent_entries(2)
        if recent:
            days = "\n\n".join(text.strip() for _day, text in recent)
            parts.append(f"## Your Recent Days (from your journal)\n{days}")
        today = journal().today_line()
        if today:
            parts.append(f"## Today So Far\n{today}")
    except Exception:
        logger.debug("journal recall skipped", exc_info=True)
    return "\n\n".join(parts)
