"""
Lightweight Retrieval-Augmented Generation (RAG) system for current events and web knowledge.

Provides per-message web search facts to enrich Koroki's context about:
  - Recent game releases and updates
  - Current events and news
  - Industry developments
  - Cultural references

Zero VRAM cost: All search happens on CPU via DuckDuckGo search API (free, no auth needed).
Integration: Orchestrator injects top 2-3 facts invisibly into prompt before Brain generation.

Features:
  - Automatic fact extraction from search results
  - Deduplication and relevance filtering
  - Optional enable/disable via config
  - Graceful degradation if search fails
  - Request timeout (2s) to prevent blocking TTS
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

logger = logging.getLogger("orchestrator.rag")

_RECENCY_TERMS = (
    "latest", "recent", "new", "newest", "today", "yesterday", "tomorrow",
    "this week", "this month", "currently", "current", "right now", "breaking",
    "just announced", "just released", "release date", "launch date", "patch notes",
    "update", "updated", "version", "hotfix", "roadmap",
)
_LOOKUP_LEADS = (
    "what is", "who is", "where is", "when is", "when did", "did", "does",
    "do you know", "have you heard", "are you aware", "look up", "search",
    "check", "find",
)
_TOPIC_TERMS = (
    "news", "event", "game", "movie", "show", "anime", "manga", "dlc", "patch",
    "update", "release", "announcement", "company", "studio", "character", "season",
    "episode", "steam", "console", "gpu", "driver",
)
_CHAT_ONLY_PATTERNS = (
    "what do you think",
    "thoughts on",
    "do you like",
    "would you like",
    "how are you",
    "hello",
    "hi ",
)


def _extract_search_facts(
    query: str,
    results: list[dict],
    max_facts: int = 3,
) -> list[str]:
    """Extract concise factual statements from DuckDuckGo search results."""
    if not results or max_facts <= 0:
        return []

    facts: list[str] = []

    for result in results[:5]:  # Check first 5 results
        title = str(result.get("title", "")).strip()
        body = str(result.get("body", "")).strip()

        if not title or not body:
            continue

        # Clean up body text: remove extra whitespace, truncate to ~200 chars
        body_clean = re.sub(r"\s+", " ", body).strip()
        if len(body_clean) > 200:
            # Try to cut at a sentence boundary
            cut_point = body_clean.rfind(".", 0, 200)
            if cut_point > 100:
                body_clean = body_clean[:cut_point + 1]
            else:
                body_clean = body_clean[:200].rstrip() + "..."

        # Format as a fact
        fact = f"{title}: {body_clean}"
        facts.append(fact)

        if len(facts) >= max_facts:
            break

    return facts


async def search_web(
    query: str,
    max_facts: int = 3,
    timeout_seconds: float = 2.0,
) -> list[str]:
    """
    Search the web for facts related to a query.
    
    Uses DuckDuckGo Instant Answer API (free, no authentication needed).
    Returns a list of factual statements suitable for prompt injection.
    
    Returns empty list if:
    - Query is too short or empty
    - Search times out
    - Search fails
    """
    if not query or len(query) < 3:
        return []

    if httpx is None:
        logger.warning("httpx not available; RAG disabled")
        return []

    # Identify if this is a question or a search query
    query_clean = query.strip()

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            # DuckDuckGo Instant Answer API
            response = await client.get(
                "https://api.duckduckgo.com/",
                params={
                    "q": query_clean,
                    "format": "json",
                    "no_html": 1,
                    "skip_disambig": 1,
                },
            )
            response.raise_for_status()
            data = response.json()

            # Check for direct answer first (highest confidence)
            if data.get("Answer"):
                answer = str(data.get("Answer", "")).strip()
                if answer and len(answer) > 10:
                    return [answer[:200]]

            # Fall back to abstract (Wikipedia summary)
            if data.get("AbstractText"):
                abstract = str(data.get("AbstractText", "")).strip()
                if abstract and len(abstract) > 20:
                    title = str(data.get("AbstractTitle", "Results")).strip()
                    fact = f"{title}: {abstract[:180]}"
                    return [fact]

            # Fall back to related topics (multiple results)
            related = data.get("RelatedTopics", [])
            if related:
                facts = _extract_search_facts(query_clean, related, max_facts)
                return facts

            return []

    except asyncio.TimeoutError:
        logger.debug("[RAG] Search timed out for query: %s", query_clean)
        return []
    except Exception as exc:
        logger.debug("[RAG] Search failed for query %s: %s", query_clean, exc)
        return []


def _should_search_for_query(message: str) -> bool:
    """Determine if a user message should trigger a web search."""
    lowered = re.sub(r"\s+", " ", message.lower()).strip()
    if len(lowered) < 8:
        return False

    if any(lowered.startswith(pattern) for pattern in ("hi", "hello", "hey")) and "?" not in lowered:
        return False

    recency_hit = any(term in lowered for term in _RECENCY_TERMS)
    topic_hit = any(term in lowered for term in _TOPIC_TERMS)
    lookup_hit = any(lowered.startswith(prefix) for prefix in _LOOKUP_LEADS)
    contains_question = "?" in lowered

    # Casual opinion prompts without current-info framing should stay local.
    if any(pattern in lowered for pattern in _CHAT_ONLY_PATTERNS) and not recency_hit:
        if not lookup_hit or not topic_hit:
            return False

    # Strongest trigger: time-sensitive topic.
    if recency_hit and (topic_hit or lookup_hit or contains_question):
        return True

    # Explicit search/look-up prompts can trigger RAG even without recency wording.
    if lookup_hit and topic_hit and contains_question:
        return True

    # Requests with concrete release/update/news wording should trigger even if not phrased as a question.
    if topic_hit and any(term in lowered for term in ("release", "released", "announcement", "patch", "update", "news")):
        return recency_hit or lookup_hit

    return False


def _extract_search_query_from_message(message: str) -> Optional[str]:
    """Extract the most relevant search query from a user message."""
    # Remove action markers
    cleaned = re.sub(r"\*[^*]+\*|\([^)]+\)|\[[^\]]+\]", "", message).strip()

    # If it's a question, use the whole thing (minus question mark)
    if "?" in cleaned:
        return cleaned.rstrip("?").strip()

    # Otherwise, try to extract the key phrase
    # Simple heuristic: last 10-15 words of the message
    words = cleaned.split()
    if len(words) > 15:
        return " ".join(words[-15:])

    return cleaned if cleaned else None


async def inject_rag_facts(
    message: str,
    core_facts: list[str] | None = None,
    max_facts: int = 2,
    rag_enabled: bool = True,
) -> list[str]:
    """
    Optionally search the web and inject facts into core_facts.
    
    Returns updated core_facts list with RAG results prepended (if any).
    If RAG is disabled or search fails, returns original core_facts.
    """
    if not rag_enabled or not _should_search_for_query(message):
        return core_facts or []

    search_query = _extract_search_query_from_message(message)
    if not search_query:
        return core_facts or []

    try:
        search_facts = await search_web(search_query, max_facts=max_facts, timeout_seconds=2.0)
        if search_facts:
            logger.info(
                "[RAG] Injected %d fact(s) for query: %s",
                len(search_facts),
                search_query[:60],
            )
            existing = core_facts or []
            # Prepend search facts, keep existing facts, deduplicate
            combined = search_facts + existing
            # Simple dedup by string uniqueness
            seen = set()
            deduped = []
            for fact in combined:
                if fact not in seen:
                    deduped.append(fact)
                    seen.add(fact)
            return deduped[:max_facts + len(existing)]
    except Exception as exc:
        logger.debug("[RAG] Failed to inject facts: %s", exc)

    return core_facts or []
