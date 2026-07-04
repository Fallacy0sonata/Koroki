"""
DPO preference data logger.

Logs every Koroki response with its emotional context to data/dpo_preferences/.
Two files:
  responses.jsonl   — append-only; one entry per Brain response
  labels.json       — {request_id: "chosen"|"rejected"}; updated by Discord reactions

When enough pairs accumulate, run DPO fine-tuning with unsloth on this data.
Format is compatible with standard (prompt, chosen, rejected) triplet extraction.

Usage:
  await log_response(...)         # called from chat.py after every Brain call
  await set_preference(id, ...)   # called from discord_bot.py on reaction events
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("orchestrator.preference_logger")

_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "dpo_preferences"
_RESPONSES_FILE = _DATA_DIR / "responses.jsonl"
_LABELS_FILE = _DATA_DIR / "labels.json"

_file_lock = asyncio.Lock()


async def log_response(
    request_id: str,
    user_id: str,
    user_message: str,
    response_text: str,
    intended_emotion: str,
    expressed_emotion: str | None,
    relationship_score: int,
    is_owner: bool,
) -> None:
    """Append a response entry. Fire-and-forget from chat.py."""
    if not response_text.strip():
        return

    entry = {
        "ts": int(time.time()),
        "request_id": request_id,
        "user_id": user_id,
        "user_message": user_message[:512],
        "response": response_text,
        "intended_emotion": intended_emotion,
        "expressed_emotion": expressed_emotion,
        "relationship_score": relationship_score,
        "is_owner": is_owner,
        "emotion_diverged": (
            expressed_emotion is not None and expressed_emotion != intended_emotion
        ),
    }
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False)
        async with _file_lock:
            with open(_RESPONSES_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as exc:
        logger.warning("preference_logger write failed: %s", exc)


async def set_preference(request_id: str, preference: str) -> bool:
    """
    Mark a logged response as 'chosen' or 'rejected'.
    Called by discord_bot.py when a user reacts with 👍 or 👎.
    Returns True if the request_id was found in the labels file.
    """
    if preference not in ("chosen", "rejected"):
        return False
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        async with _file_lock:
            labels: dict[str, str] = {}
            if _LABELS_FILE.exists():
                with open(_LABELS_FILE, encoding="utf-8") as f:
                    labels = json.load(f)
            labels[request_id] = preference
            with open(_LABELS_FILE, "w", encoding="utf-8") as f:
                json.dump(labels, f, ensure_ascii=False, indent=2)
        logger.info("Preference set: request_id=%s preference=%s", request_id, preference)
        return True
    except Exception as exc:
        logger.warning("preference_logger label update failed: %s", exc)
        return False


def get_label(request_id: str) -> str | None:
    """Sync helper — read a single preference label without the async lock."""
    try:
        if not _LABELS_FILE.exists():
            return None
        with open(_LABELS_FILE, encoding="utf-8") as f:
            return json.load(f).get(request_id)
    except Exception:
        return None
