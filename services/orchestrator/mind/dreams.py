"""Dreams — REM replay made real. On waking, she has actually dreamt something.

The sleep cycle has had a "dream replay" hook slot since Phase 2C; this fills it.
Material is drawn from her REAL lived record (yesterday's + today's journal events,
plus her highest-charge recent memories) and recombined by one Brain call into a short,
surreal, first-person dream — logged to the journal (KIND_DREAM, renders as "Dreamt"
in the day entry) so she can tell you what she dreamt, truthfully.

Causal-chain honest: the dream is CAUSED by what the day put into her — not invented
from nothing. When the Brain is unreachable at wake time, a deterministic template
fallback composes a minimal fragment-dream instead (never blocks, never fails loudly).

Trigger: meta/sleep_cycle wake callback → dream_on_wake() (runs in a daemon thread —
the wake transition can happen inside a chat request's tick; the LLM call must never
block that path). Guards: slept ≥ 20 min, at most one dream per 4 h.
"""

from __future__ import annotations

import logging
import random
import threading
import time

import httpx

from shared.utils.config import get_settings
from .journal import journal, KIND_DREAM

logger = logging.getLogger("orchestrator.mind.dreams")

_MIN_SLEEP_S = 20 * 60
_DREAM_COOLDOWN_S = 4 * 3600

_state_lock = threading.Lock()
_sleep_started_ts: float | None = None
_last_dream_ts: float = 0.0

_DREAM_SYSTEM = (
    "You are Koroki, just woken up, still half in the dream. Write the dream you had — "
    "2 or 3 sentences, first person, present tense, surreal the way real dreams are: "
    "familiar things in wrong places, logic that made sense until you woke. Weave in the "
    "fragments below without listing them. No preamble, no explanation, just the dream."
)


def note_sleep_start() -> None:
    global _sleep_started_ts
    with _state_lock:
        _sleep_started_ts = time.time()


def dream_on_wake() -> None:
    """Called by the sleep cycle on wake. Spawns the dream in a background thread."""
    cfg = get_settings().get("mind", {}).get("dreams", {})
    if not bool(cfg.get("enabled", True)):
        return
    now = time.time()
    with _state_lock:
        started = _sleep_started_ts
        last = _last_dream_ts
    if started is None or (now - started) < _MIN_SLEEP_S:
        return
    if now - last < _DREAM_COOLDOWN_S:
        return
    threading.Thread(target=_generate_and_log, daemon=True, name="koroki-dream").start()


def _gather_fragments(max_fragments: int = 5) -> list[str]:
    """Real material from her lived record — journal events + high-charge memories."""
    from .journal import _local_day  # same module family; local-day helper
    frags: list[str] = []
    try:
        j = journal()
        events = j.read_day(_local_day()) + j.read_day(_local_day(time.time() - 86400))
        pool = [e.get("text", "") for e in events
                if e.get("kind") in ("activity", "world_event", "interaction", "thought", "sing")]
        frags.extend(t for t in pool if t)
    except Exception:
        pass
    try:
        from .memory import get_memory
        nodes = sorted(get_memory().all_nodes(), key=lambda n: n.importance, reverse=True)[:8]
        frags.extend(n.content for n in nodes[:4])
    except Exception:
        pass
    random.shuffle(frags)
    return frags[:max_fragments]


def _generate_and_log() -> None:
    global _last_dream_ts
    fragments = _gather_fragments()
    dream = _llm_dream(fragments) or _template_dream(fragments)
    if not dream:
        return
    with _state_lock:
        _last_dream_ts = time.time()
    journal().log_event(KIND_DREAM, dream)
    logger.info("dream logged: %r", dream[:120])


def _llm_dream(fragments: list[str]) -> str | None:
    try:
        settings = get_settings()
        url = settings["services"]["brain"]["url"] + "/v1/generate"
        frag_block = "\n".join(f"- {f}" for f in fragments) if fragments else "- a quiet, empty day"
        payload = {
            "request_id": f"dream_{int(time.time())}",
            "message": f"[internal] fragments from the day:\n{frag_block}\n\nThe dream:",
            "user_context": {
                "user_id": "__dream_generator__",
                "relationship_score": 100,
                "is_owner": False,
                "mode": "auto",
                "platform": "desktop",
                "core_facts": ["koroki_mode=dream_generation", _DREAM_SYSTEM],
            },
            "max_new_tokens": 110,
            "enable_thinking": False,
            "forbidden_phrases": [],
        }
        with httpx.Client(timeout=45.0) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            text = (r.json().get("text") or "").strip()
        if len(text) < 20:
            return None
        return text
    except Exception as exc:
        logger.info("LLM dream unavailable (%s) — using template fallback", exc)
        return None


def _template_dream(fragments: list[str]) -> str:
    """Deterministic minimal fallback — still made of her real day."""
    if not fragments:
        return ""
    picks = fragments[:2]
    if len(picks) == 1:
        return f"a blurry dream that kept circling back to this: {picks[0]}"
    return (f"a tangled dream where two things kept blending into each other — "
            f"{picks[0]}, and then somehow {picks[1]}")
