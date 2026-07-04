"""
Guillotine — streaming token scanner.

Scans each chunk of text from the brain stream for forbidden patterns
(training data leakage, prompt injection artifacts). If a forbidden pattern
is detected, raises GuillotineViolation immediately. The caller MUST stop
the stream and emit an error event — never let poisoned tokens reach the user.

Uses a rolling buffer to catch patterns that span chunk boundaries.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

logger = logging.getLogger("orchestrator.guillotine")

_DEFAULT_FORBIDDEN = [
    "Assistant:",
    "User:",
    "System:",
    "<|im_start|>",
    "<|im_end|>",
    "<|endoftext|>",
    "### Human:",
    "### Assistant:",
    "[INST]",
    "[/INST]",
]


def _load_forbidden() -> list[str]:
    try:
        from shared.utils.config import get_settings
        return get_settings().get("guillotine", {}).get("forbidden_tokens", _DEFAULT_FORBIDDEN)
    except Exception:
        return _DEFAULT_FORBIDDEN


class GuillotineViolation(Exception):
    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        super().__init__(f"Forbidden token detected in stream: {repr(pattern)}")


class StreamingGuillotine:
    """
    Wraps a token stream. Call scan(chunk) on each chunk.
    Raises GuillotineViolation if any forbidden pattern appears.
    The rolling buffer detects patterns split across chunk boundaries.
    """

    def __init__(self, forbidden: list[str] | None = None) -> None:
        self._forbidden = forbidden if forbidden is not None else _load_forbidden()
        self._max_pattern_len = max((len(p) for p in self._forbidden), default=0)
        self._buffer = ""

    def scan(self, chunk: str) -> str:
        """Scan a chunk. Returns it unchanged if safe. Raises GuillotineViolation if not."""
        self._buffer += chunk

        for pattern in self._forbidden:
            if pattern in self._buffer:
                logger.warning(
                    "Guillotine triggered: pattern %r found in stream buffer", pattern
                )
                raise GuillotineViolation(pattern)

        # Trim buffer — keep only the tail needed to catch cross-chunk patterns
        tail_len = self._max_pattern_len - 1
        if tail_len > 0 and len(self._buffer) > tail_len:
            self._buffer = self._buffer[-tail_len:]
        elif tail_len == 0:
            self._buffer = ""

        return chunk

    def reset(self) -> None:
        self._buffer = ""

    async def wrap(self, stream: AsyncIterator[str]) -> AsyncIterator[str]:
        """Async generator that wraps a text stream with guillotine scanning."""
        async for chunk in stream:
            yield self.scan(chunk)
        self.reset()
