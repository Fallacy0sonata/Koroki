"""
Sleep cycle — what happens to mind/body while she's asleep.

Phase 2C component. Per docs/koroki_subsystem_atlas.md §7.1:
  - Memory consolidation: episodic memories from waking life integrate
    into self-narrative; less-important ones decay faster
  - Receptor sensitivity recovery (already handled by endocrine.tick_receptor)
  - Cortisol baseline normalization
  - "Dreams" — REM phase replays high-importance memories
    (Phase 2C: lightweight — Phase 3 expands)

For Phase 2C MVP:
  - Hook into sleep.on_sleep() to run consolidation when ASLEEP begins
  - Hook into sleep.on_wake() to log what happened

Implementation strategy:
  - Don't run heavy computation in the callback (it's called from sleep tick).
    Instead, mark consolidation pending and run on next call to consolidate().
  - Periodic consolidate() can be called from a background task or from main
    tick loop in future autonomous loop. For Phase 2C, chat.py calls it
    whenever sleep state changes.

═══════════════════════════════════════════════════════════════════════════════
🐛 PREDICTED BUGS / WATCH-FOR-WHEN-LIVE-TESTING:

SC1. "After sleep, no memories were consolidated — recent memories still
      have same importance / no semantic facts emerged"
     Look at: _consolidate() — the logic that promotes high-importance
     episodic to semantic / decays low-importance. Phase 2C MVP is
     intentionally lightweight; semantic memory layer is Phase 3.

SC2. "Consolidation fires every tick during ASLEEP (wasteful)"
     Look at: _consolidation_pending flag + consolidate() gating. Should
     fire ONCE per sleep cycle, not continuously.

SC3. "Dream replay never happens"
     Look at: this is Phase 2C MVP; replay is just a hook for now. Phase 3
     will actually fire memory recall events during ASLEEP REM phases.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ..body.sleep import get_sleep, SleepState
from ..mind.memory import get_memory

logger = logging.getLogger("orchestrator.meta.sleep_cycle")


class SleepCycle:
    """Manages what happens to mind/body during sleep transitions."""

    def __init__(self):
        self._lock = threading.Lock()
        self._consolidation_pending = False
        self._wired = False

    def wire(self) -> None:
        """Wire up callbacks on the sleep system. Idempotent."""
        if self._wired:
            return
        sleep = get_sleep()
        sleep.on_sleep(self._on_sleep)
        sleep.on_wake(self._on_wake)
        self._wired = True
        logger.info("Sleep cycle wired to sleep system")

    def _on_sleep(self) -> None:
        """She just entered ASLEEP. Mark consolidation as pending."""
        with self._lock:
            self._consolidation_pending = True
        logger.info("Sleep cycle: consolidation marked pending")
        try:
            from ..mind.journal import journal, KIND_SLEEP
            journal().log_event(KIND_SLEEP, "fell asleep")
        except Exception:
            pass
        try:
            from ..mind.dreams import note_sleep_start
            note_sleep_start()
        except Exception:
            pass

    def _on_wake(self) -> None:
        """She just woke up. Run consolidation if it didn't already."""
        with self._lock:
            pending = self._consolidation_pending
        try:
            from ..mind.journal import journal, KIND_SLEEP
            journal().log_event(KIND_SLEEP, "woke up")
        except Exception:
            pass
        # REM payoff: she wakes with a dream made from her real day (background thread)
        try:
            from ..mind.dreams import dream_on_wake
            dream_on_wake()
        except Exception:
            pass
        if pending:
            self.consolidate()

    def consolidate(self) -> None:
        """Run memory consolidation.

        Phase 2C MVP — minimal:
          1. Scan memories from the last waking period
          2. Identify high-importance ones (importance > 0.6)
          3. Log them as "consolidated" (Phase 3 will write to semantic layer)
          4. Low-importance memories get slight importance decay (forgetting)

        Side effects on body:
          - Mark consolidation as done
        """
        with self._lock:
            if not self._consolidation_pending:
                return
            self._consolidation_pending = False

        # the journal compiles finished days into durable autobiographical entries here —
        # this is the "Phase 3 semantic layer" the MVP stub deferred
        try:
            from ..mind.journal import journal
            journal().consolidate_pending()
        except Exception:
            logger.warning("journal consolidation skipped", exc_info=True)

        memory = get_memory()
        all_nodes = memory.all_nodes()
        # Find memories from the last ~16h that haven't been consolidated yet
        cutoff_ts = time.time() - (16 * 3600)
        recent = [n for n in all_nodes if n.timestamp > cutoff_ts]

        high_importance = [n for n in recent if n.importance >= 0.6]
        low_importance = [n for n in recent if n.importance < 0.4]

        # Phase 2C MVP: just log + apply gentle decay to low-importance
        # (forgetting). Phase 3 will write semantic-layer entries.
        for node in low_importance:
            node.importance *= 0.9
            node.importance = max(0.05, node.importance)
        logger.info(
            "Sleep cycle consolidation: %d recent memories scanned, "
            "%d high-importance retained, %d low-importance decayed",
            len(recent), len(high_importance), len(low_importance),
        )

    def is_consolidation_pending(self) -> bool:
        with self._lock:
            return self._consolidation_pending


# ────────────────────────────────────────────────────────────────────
# Module-level singleton
# ────────────────────────────────────────────────────────────────────

_INSTANCE: SleepCycle | None = None
_INSTANCE_LOCK = threading.Lock()


def get_sleep_cycle() -> SleepCycle:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = SleepCycle()
                _INSTANCE.wire()
    return _INSTANCE
