"""
Background thought generator for Koroki.

Fires every hour, calls the brain to generate one short internal thought,
saves it to data/thoughts/history.json.

History is capped at 24 entries (24 hours). Older thoughts are pruned on write.
The thought file is the primary source for get_current_thought(); the static pool
in thoughts.py is the fallback when the file is missing or too stale.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from shared.utils.config import get_settings

logger = logging.getLogger("orchestrator.thoughts")

_repo_root = Path(__file__).resolve().parents[2]
THOUGHT_DIR = _repo_root / "data" / "thoughts"
THOUGHT_FILE = THOUGHT_DIR / "history.json"

HISTORY_MAX = 24
_INTERVAL_MIN_S = 2700   # 45 min minimum
_INTERVAL_MAX_S = 6600   # 110 min maximum


def _next_interval() -> float:
    """Organic jitter — next thought arrives between 45 and 110 minutes from now."""
    return random.uniform(_INTERVAL_MIN_S, _INTERVAL_MAX_S)

# Prompt fed to the brain to generate a single thought.
# Deliberately open-ended — Koroki should think about the world, not just herself.
_THOUGHT_SYSTEM = (
    "You are Koroki. Generate a single brief internal thought — one line only. "
    "No dialogue, no action markers, no explanation. Just the thought itself, "
    "written as if it appeared in your mind mid-afternoon when nothing particular is happening. "
    "The thought can be about anything: something you noticed, a question you can't answer, "
    "something trivial or philosophical, an observation about people, a fragment of an idea. "
    "Do not always think about yourself — let it be about the world, something you read, "
    "something you heard, how something works, why people do things the way they do."
)

_TIME_LABELS = {
    range(0, 6): "late night",
    range(6, 12): "morning",
    range(12, 17): "afternoon",
    range(17, 22): "evening",
    range(22, 24): "late night",
}


def _time_of_day() -> str:
    h = datetime.now(timezone.utc).hour
    for r, label in _TIME_LABELS.items():
        if h in r:
            return label
    return "afternoon"


def load_history() -> list[dict]:
    """Load thought history from disk. Returns [] if file missing or corrupt."""
    try:
        with open(THOUGHT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


def save_history(history: list[dict]) -> None:
    THOUGHT_DIR.mkdir(parents=True, exist_ok=True)
    with open(THOUGHT_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def append_thought(thought: str) -> None:
    """Append a new thought to history, pruning entries older than HISTORY_MAX hours."""
    now = time.time()
    history = load_history()
    history.append({"ts": now, "thought": thought})
    cutoff = now - HISTORY_MAX * 3600
    history = [e for e in history if e.get("ts", 0) >= cutoff]
    save_history(history)
    logger.info("Thought saved: %r (history: %d entries)", thought, len(history))
    # thoughts also flow into the durable experience journal (history.json prunes at 24h;
    # the journal is where her life actually accumulates)
    try:
        from .mind.journal import journal, KIND_THOUGHT
        journal().log_event(KIND_THOUGHT, thought, ts=now)
    except Exception:
        logger.debug("journal thought write skipped", exc_info=True)


async def _generate_thought() -> str | None:
    """Call the brain to generate one fresh thought. Returns None on failure."""
    settings = get_settings()
    brain_url = settings["services"]["brain"]["url"] + "/v1/generate"
    tod = _time_of_day()

    # Bring in nervous system state so thoughts emerge from her current internal state
    try:
        from .nervous_system import get_state, build_state_block
        ns_state = get_state()
        state_block = build_state_block(ns_state)
    except Exception:
        state_block = None

    core_facts = [
        "koroki_mode=internal_thought_generation",
        f"time_of_day={tod}",
    ]
    if state_block:
        core_facts.append(state_block)

    payload = {
        "request_id": f"thought_{int(time.time())}",
        "message": f"[internal] {tod} — one sentence, what's actually in your head right now. not meta. just the thing.",
        "user_context": {
            "user_id": "__thought_generator__",
            "relationship_score": 100,
            "is_owner": False,
            "mode": "auto",
            "platform": "desktop",
            "core_facts": core_facts,
        },
        "max_new_tokens": 60,
        "enable_thinking": True,
        "forbidden_phrases": [],
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(brain_url, json=payload)
            r.raise_for_status()
            text = (r.json().get("text") or "").strip()
            if not text:
                return None
            # Strip action markers and keep only the first sentence
            import re
            text = re.sub(r"\*[^*\n]+\*", "", text).strip()
            # Take only the first sentence if multiple
            first = re.split(r"(?<=[.!?])\s+", text)[0].strip()
            return first if len(first) >= 8 else text
    except Exception as exc:
        logger.warning("Thought generation failed: %s", exc)
        return None


async def run_thought_loop() -> None:
    """Background task — generates a new thought on an organic interval (45–110 min)."""
    await asyncio.sleep(15)  # brief startup delay so the brain finishes loading
    while True:
        thought = await _generate_thought()
        if thought:
            append_thought(thought)
        else:
            logger.warning("Thought generation returned nothing — keeping current thought")
        interval = _next_interval()
        logger.debug("Next thought in %.0f minutes", interval / 60)
        await asyncio.sleep(interval)
