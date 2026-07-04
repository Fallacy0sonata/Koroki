"""
Rumination queue — background processing of conversations and events.

Conversations don't end when they end. Items enter this queue with a resonance
score. High resonance items surface faster; low resonance items quietly drop.

When an item surfaces, it becomes a "pending rumination" available for injection
into the next system prompt — the basis of "I've been thinking about what you said."

Storage: data/nervous_system/rumination.json
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger("orchestrator.nervous_system.rumination")

_repo_root = Path(__file__).resolve().parents[3]
_RUMINATION_FILE = _repo_root / "data" / "nervous_system" / "rumination.json"

_MAX_QUEUE_SIZE = 50
_CYCLE_INTERVAL_S = 600  # surface-check every 10 minutes
_BASE_SURFACE_PROB = 0.12  # per-cycle probability for resonance=1.0 item

# Max age before item expires (seconds). Low-resonance items expire sooner.
# Formula: max_age = 3600 * (1 + resonance * 23)  → 1h for resonance=0, 24h for resonance=1
def _max_age_s(resonance: float) -> float:
    return 3600.0 * (1.0 + resonance * 23.0)


@dataclass
class RuminationItem:
    item_id: str
    content: str          # natural language summary of the conversation/event
    resonance: float      # 0–1: how much this connected to her state when it entered
    entered_at: float     # epoch seconds
    user_id: str          # who triggered it (empty string if activity event)
    cycles_checked: int = 0


def _load() -> list[RuminationItem]:
    try:
        with open(_RUMINATION_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [RuminationItem(**d) for d in data if isinstance(d, dict)]
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return []


def _save(queue: list[RuminationItem]) -> None:
    _RUMINATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_RUMINATION_FILE, "w", encoding="utf-8") as f:
        json.dump([asdict(item) for item in queue], f, indent=2)


class RuminationQueue:
    def __init__(self) -> None:
        self._queue: list[RuminationItem] = _load()
        self._surfaced: str | None = None     # content of current surfaced item
        self._surfaced_user: str | None = None
        self._surfaced_at: float = 0.0
        self._last_tick: float = 0.0

    def add(self, content: str, resonance: float, user_id: str = "") -> None:
        """Add a new item to the queue. Drops lowest-resonance if queue is full."""
        resonance = max(0.0, min(1.0, resonance))
        item = RuminationItem(
            item_id=f"rum_{int(time.time() * 1000)}",
            content=content,
            resonance=resonance,
            entered_at=time.time(),
            user_id=user_id,
        )
        self._queue.append(item)
        if len(self._queue) > _MAX_QUEUE_SIZE:
            # Drop lowest-resonance item
            self._queue.sort(key=lambda x: x.resonance, reverse=True)
            self._queue = self._queue[:_MAX_QUEUE_SIZE]
        _save(self._queue)
        logger.debug("Rumination added: resonance=%.2f content='%.50s'", resonance, content)

    def tick(self) -> None:
        """
        Process one cycle. Called every ~10 minutes from the nervous system loop.
        Expires old items and maybe surfaces one.
        """
        now = time.time()
        if now - self._last_tick < _CYCLE_INTERVAL_S:
            return
        self._last_tick = now

        # Expire old items
        kept: list[RuminationItem] = []
        for item in self._queue:
            age = now - item.entered_at
            if age > _max_age_s(item.resonance):
                logger.debug("Rumination expired: %.50s", item.content)
                continue
            kept.append(item)
        self._queue = kept

        # Try to surface one item (don't surface if already have one pending)
        if self._surfaced and now - self._surfaced_at < 3600:
            _save(self._queue)
            return

        for item in sorted(self._queue, key=lambda x: x.resonance, reverse=True):
            prob = _BASE_SURFACE_PROB * item.resonance
            item.cycles_checked += 1
            if random.random() < prob:
                self._surfaced = item.content
                self._surfaced_user = item.user_id
                self._surfaced_at = now
                self._queue.remove(item)
                logger.info("Rumination surfaced: %.80s", item.content)
                break

        _save(self._queue)

    def consume_surfaced(self) -> tuple[str, str] | None:
        """Return (content, user_id) if there is a surfaced item, then clear it. None otherwise."""
        if not self._surfaced:
            return None
        result = (self._surfaced, self._surfaced_user or "")
        self._surfaced = None
        self._surfaced_user = None
        return result

    def peek_surfaced(self) -> tuple[str, str] | None:
        """Return (content, user_id) without consuming."""
        if not self._surfaced:
            return None
        return (self._surfaced, self._surfaced_user or "")

    def queue_size(self) -> int:
        return len(self._queue)


# Module-level singleton
_rumination_queue = RuminationQueue()


def add_rumination(content: str, resonance: float, user_id: str = "") -> None:
    """Public API: add a conversation/event to the rumination queue."""
    _rumination_queue.add(content, resonance, user_id)


def tick_rumination() -> None:
    """Called from engine run_loop every cycle. Processes expiry and surface events."""
    _rumination_queue.tick()


def consume_surfaced_rumination() -> tuple[str, str] | None:
    """Called from serializer: returns surfaced (content, user_id) and clears it."""
    return _rumination_queue.consume_surfaced()


def peek_surfaced_rumination() -> tuple[str, str] | None:
    """Non-destructive peek at surfaced item."""
    return _rumination_queue.peek_surfaced()


def get_queue_size() -> int:
    return _rumination_queue.queue_size()
