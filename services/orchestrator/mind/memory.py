"""
Memory stream — Park-style episodic memory with importance scoring.

Architecture per atlas §4.1 and master_queue.md (cribbed from Stanford
Generative Agents, Letta/MemGPT, and the Phase 2B research dive).

Components:
  - MemoryNode: a single episodic memory with content, body state at write,
    importance, embedding (placeholder for Phase 2C), access tracking.
  - MemoryStream: append-only collection. Retrieval scoring is
    α·recency + β·importance + γ·relevance.

Recency: exp(-age_hours · decay)
Importance: pre-computed at write time from body state during the event
Relevance: text overlap for Phase 2B MVP (Phase 2C: real embeddings)

Why importance from body state:
  An event that spiked cortisol by 0.3 was important — body said so. An event
  that produced mild dopamine spike was meh. We don't ask the LLM to score
  importance; the body already did via what it released. This is captain-in-
  cabin all the way down.

Memory ↔ body feedback:
  When a memory is recalled, it fires a small body event based on the recalled
  memory's body_state_at_write. Recalling a warm moment releases small oxytocin;
  recalling a conflict releases small cortisol. Includes anti-loop cooldown.

═══════════════════════════════════════════════════════════════════════════════
🐛 PREDICTED BUGS / WATCH-FOR-WHEN-LIVE-TESTING:

1. Symptom: Memory store fills up rapidly (every message becomes a memory),
   eventually slowing chat or eating disk.
   Look at: MIN_IMPORTANCE_TO_WRITE threshold + auto_write_from_body() —
   we're filtering by body-state intensity. If the threshold is too low,
   filter doesn't drop enough.

2. Symptom: Recalls return the same memory over and over.
   Look at: MemoryNode.last_accessed_ts update logic in score_and_retrieve.
   Recency-based score should decay AFTER access so recently-recalled memories
   drop in priority briefly. Verify last_accessed_ts is being updated.

3. Symptom: Memory recall feedback creates oscillation (recall warm → oxytocin
   up → re-recall warm → oxytocin up more → ...)
   Look at: RECALL_COOLDOWN_SECONDS — a recalled memory can't fire body feedback
   again within this window. If cooldown isn't being checked OR the cooldown
   timestamp isn't being recorded, we get oscillation.

4. Symptom: Memory importance scores are all clustered around the same value
   (everything imports as 0.5, nothing stands out).
   Look at: _compute_importance_from_body() — the formula should produce
   meaningful spread. If body_state values are all in a narrow band (because
   body hasn't been stressed yet), importance won't differentiate. May need
   to normalize against rolling history of body state.

5. Symptom: Old memories are never retrieved even when contextually relevant.
   Look at: RECENCY_DECAY_HOURS — if too aggressive, old memories effectively
   become zero-weight. Adjust upward, or boost via importance.

6. Symptom: Relevance ranking is bad — irrelevant memories come up first.
   Look at: _relevance_text_overlap() — Phase 2B MVP uses simple Jaccard-style
   text overlap. This is a known weak relevance signal. Phase 2C replaces with
   real embeddings. Until then, this is a known limitation.

7. Symptom: After brain restart, "she doesn't remember anything" even though
   we wrote memories before.
   Look at: Persistence in `data/mind/memory_stream.jsonl`. Memory stream
   should be append-only JSONL file, loaded on init. If file gets corrupted
   (malformed line), partial loading should still work — verify _load
   handles bad lines gracefully.

8. Symptom: Memory ↔ body feedback never fires (no warm-memory recall effect).
   Look at: apply_recall_feedback() — gets called from retrieve() but only
   if the recalled memory's body_state_at_write has meaningful values. If
   memories were written before endocrine was live, they have empty body_state
   and feedback can't fire. Migration consideration.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("orchestrator.mind.memory")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STREAM_PATH = _REPO_ROOT / "data" / "mind" / "memory_stream.jsonl"
# semantic vectors sidecar (Phase 2C embeddings — one JSONL line per node id)
_VECTORS_PATH = _REPO_ROOT / "data" / "mind" / "memory_embeddings.jsonl"

# ── Tuning constants ──────────────────────────────────────────────────────
# Recency: weight halves every RECENCY_DECAY_HOURS hours.
RECENCY_DECAY_HOURS = 36.0  # 1.5 days half-life — memories from 1-2 days ago still surface

# Retrieval score weights — research-validated approximation.
ALPHA_RECENCY = 0.35
BETA_IMPORTANCE = 0.40
GAMMA_RELEVANCE = 0.25

# Below this importance, don't auto-write the memory.
# 0..1 scale where 0 is "nothing happened" and 1 is "life-changing event."
MIN_IMPORTANCE_TO_WRITE = 0.25

# Re-recalling the same memory within this window doesn't fire body feedback.
# Prevents oscillation (warm recall → oxytocin up → re-recall → oxytocin up more).
RECALL_COOLDOWN_SECONDS = 60.0

# Body-state hormones that contribute to importance scoring at write time.
# The intuition: events that spike cortisol/dopamine/oxytocin are important;
# events that don't barely register.
_IMPORTANCE_KEYS_POSITIVE = ["oxytocin", "dopamine_phasic", "norepinephrine"]
_IMPORTANCE_KEYS_NEGATIVE = ["cortisol"]


@dataclass
class MemoryNode:
    """A single episodic memory."""

    id: str
    content: str                          # natural-language description of the event
    timestamp: float                      # when it happened
    importance: float                     # 0..1, pre-computed at write time
    body_state_at_write: dict[str, float] # snapshot of hormone levels at write
    source_user_id: str = ""              # who was involved (if any)
    tags: list[str] = field(default_factory=list)
    last_accessed_ts: float = 0.0
    access_count: int = 0
    last_feedback_ts: float = 0.0         # last time body feedback fired from this memory


@dataclass
class RecallResult:
    """A memory retrieved with its score breakdown for telemetry."""

    node: MemoryNode
    score_total: float
    score_recency: float
    score_importance: float
    score_relevance: float


def _now() -> float:
    return time.time()


def _tokenize(text: str) -> set[str]:
    """Simple word-token set for text-overlap relevance. Lowercased, basic strip."""
    if not text:
        return set()
    words = re.findall(r"[a-z0-9]+", text.lower())
    # Drop very short common-word noise.
    return {w for w in words if len(w) > 2}


def _relevance_text_overlap(query: str, memory_content: str) -> float:
    """Jaccard-style overlap. Phase 2B MVP — replace with embeddings in Phase 2C."""
    q = _tokenize(query)
    m = _tokenize(memory_content)
    if not q or not m:
        return 0.0
    intersection = len(q & m)
    union = len(q | m)
    return intersection / union if union else 0.0


def _recency_score(age_seconds: float) -> float:
    """exp-decay recency. Half-life = RECENCY_DECAY_HOURS hours."""
    age_hours = age_seconds / 3600.0
    decay = math.log(2) / RECENCY_DECAY_HOURS
    return math.exp(-decay * age_hours)


def _compute_importance_from_body(body_state: dict[str, float]) -> float:
    """Score 0..1 based on body activation at write time.

    Logic: importance reflects "how much body got moved by this event."
    Both positive (oxytocin, dopamine, NE) and negative (cortisol) reactions
    raise importance. The MAX deviation from baseline drives the score.

    Body state should be the raw hormone levels at event time.
    """
    if not body_state:
        return 0.0
    # Baselines (matches endocrine.py defaults).
    baselines = {
        "cortisol": 0.3, "dopamine_tonic": 0.4, "dopamine_phasic": 0.0,
        "oxytocin": 0.3, "serotonin": 0.7, "norepinephrine": 0.3, "melatonin": 0.0,
    }
    deviations = []
    for key in _IMPORTANCE_KEYS_POSITIVE + _IMPORTANCE_KEYS_NEGATIVE:
        level = body_state.get(key, baselines.get(key, 0.0))
        baseline = baselines.get(key, 0.0)
        deviations.append(abs(level - baseline))
    # Top-2 deviation average — single hormone spike shouldn't max out importance;
    # need broader body engagement.
    deviations.sort(reverse=True)
    score = sum(deviations[:2]) / 2 if deviations else 0.0
    # Scale: deviations of 0.5+ are very strong; saturate to 1.0.
    return min(1.0, score * 2)


class MemoryStream:
    """Single-instance, threadsafe episodic memory store.

    Persistence: append-only JSONL at data/mind/memory_stream.jsonl.
    On startup, full stream is loaded into memory (acceptable since we cap
    importance for write and memories are small). Phase 3+ may add tiered
    storage if volume grows past ~10K nodes.
    """

    def __init__(self, stream_path: Path | None = None, vectors_path: Path | None = None):
        self._lock = threading.Lock()
        self._nodes: list[MemoryNode] = []
        self._stream_path = stream_path or _STREAM_PATH
        self._vectors_path = vectors_path or _VECTORS_PATH
        self._vectors: dict[str, list[float]] = {}
        self._load()
        self._load_vectors()
        # backfill vectors for pre-embedding nodes in the background (never blocks startup)
        threading.Thread(target=self._backfill_vectors, daemon=True,
                         name="memory-embed-backfill").start()

    # ------------------------------------------------------------------
    # Semantic vectors (Phase 2C)
    # ------------------------------------------------------------------

    def _load_vectors(self) -> None:
        try:
            with open(self._vectors_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        self._vectors[rec["id"]] = rec["v"]
                    except Exception:
                        continue
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("vector sidecar load failed: %s", exc)

    def _store_vector(self, node_id: str, content: str) -> None:
        """Embed one node's content and persist its vector. Never raises."""
        try:
            from .embeddings import get_embedder
            embedder = get_embedder()
            if not embedder.available():
                return
            vecs = embedder.embed_passages([content])
            if vecs is None:
                return
            v = [round(float(x), 6) for x in vecs[0]]
            with self._lock:
                self._vectors[node_id] = v
            self._vectors_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._vectors_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"id": node_id, "v": v}) + "\n")
        except Exception as exc:
            logger.debug("vector store skipped for %s: %s", node_id[:8], exc)

    def _backfill_vectors(self) -> None:
        try:
            time.sleep(30)  # let the service come up before pulling the model in
            with self._lock:
                missing = [(n.id, n.content) for n in self._nodes if n.id not in self._vectors]
            if not missing:
                return
            logger.info("embedding backfill: %d nodes", len(missing))
            for node_id, content in missing:
                self._store_vector(node_id, content)
            logger.info("embedding backfill done")
        except Exception as exc:
            logger.warning("embedding backfill failed: %s", exc)

    # ------------------------------------------------------------------
    # Write side
    # ------------------------------------------------------------------

    def remember(
        self,
        content: str,
        body_state: dict[str, float] | None = None,
        importance: float | None = None,
        source_user_id: str = "",
        tags: Iterable[str] = (),
    ) -> MemoryNode | None:
        """Write a memory. Returns the MemoryNode, or None if filtered out.

        If `importance` is None, it's computed from body_state. Memories below
        MIN_IMPORTANCE_TO_WRITE are silently dropped (returning None) — this
        keeps the stream from filling with garbage like "user said hello."

        `tags` are free-form (e.g., ["affection", "conflict", "music"]).
        """
        body_state = body_state or {}
        if importance is None:
            importance = _compute_importance_from_body(body_state)
        if importance < MIN_IMPORTANCE_TO_WRITE:
            logger.debug(
                "Memory filtered out (importance=%.3f < threshold=%.3f): %r",
                importance, MIN_IMPORTANCE_TO_WRITE, content[:80],
            )
            return None

        node = MemoryNode(
            id=str(uuid.uuid4()),
            content=content,
            timestamp=_now(),
            importance=importance,
            body_state_at_write=dict(body_state),
            source_user_id=source_user_id,
            tags=list(tags),
        )
        with self._lock:
            self._nodes.append(node)
        self._append_to_disk(node)
        logger.info(
            "Memory written: id=%s importance=%.3f content=%r",
            node.id[:8], importance, content[:120],
        )
        # embed in the background — write-time embedding must never stall the chat path
        # (first call also loads the model, which would cost seconds inline)
        threading.Thread(target=self._store_vector, args=(node.id, content), daemon=True).start()
        # anything her body found important enough to remember is journal-worthy —
        # this is where "notable interactions" enter her autobiographical day record
        if importance >= 0.45:
            try:
                from .journal import journal, KIND_INTERACTION
                journal().log_event(
                    KIND_INTERACTION, content[:200],
                    meta={"user": source_user_id, "importance": round(importance, 3)},
                )
            except Exception:
                logger.debug("journal interaction write skipped", exc_info=True)
        return node

    def explicit_remember(
        self,
        content: str,
        importance: float,
        source_user_id: str = "",
        tags: Iterable[str] = (),
    ) -> MemoryNode:
        """Letta-style explicit remember — Koroki *chooses* to remember.

        Bypasses the importance threshold. Phase 2B places the tool-call API
        for this on the side; for now we just expose the function.
        """
        body_state: dict[str, float] = {}  # caller may pass empty
        node = MemoryNode(
            id=str(uuid.uuid4()),
            content=content,
            timestamp=_now(),
            importance=max(0.0, min(1.0, importance)),
            body_state_at_write=body_state,
            source_user_id=source_user_id,
            tags=list(tags) + ["explicit"],
        )
        with self._lock:
            self._nodes.append(node)
        self._append_to_disk(node)
        threading.Thread(target=self._store_vector, args=(node.id, content), daemon=True).start()
        logger.info("Explicit memory written: id=%s importance=%.3f", node.id[:8], importance)
        return node

    # ------------------------------------------------------------------
    # Read side
    # ------------------------------------------------------------------

    # e5 cosine calibration: unrelated pairs sit ~0.65-0.72, strong matches ~0.82-0.95.
    # Map that band onto 0..1 so relevance has the same dynamic range the score
    # weights were tuned for (raw cosines would floor every memory at ~0.7).
    _COS_LO = 0.70
    _COS_HI = 0.95

    def retrieve(
        self,
        query: str,
        top_k: int = 3,
        now_ts: float | None = None,
    ) -> list[RecallResult]:
        """Retrieve top-k memories scored by recency × importance × relevance.

        Side effect: updates last_accessed_ts on returned memories.
        Relevance: semantic embedding cosine (Phase 2C) when the node has a vector,
        text-overlap fallback otherwise (embeddings disabled / model unavailable /
        vector not yet backfilled).
        """
        ts = now_ts or _now()
        # embed the query OUTSIDE the lock — first call may load the model
        qvec = None
        try:
            from .embeddings import get_embedder
            embedder = get_embedder()
            if embedder.available():
                qvec = embedder.embed_query(query)
        except Exception:
            qvec = None

        results: list[RecallResult] = []
        with self._lock:
            for node in self._nodes:
                age = ts - node.timestamp
                recency = _recency_score(age)
                importance = node.importance
                vec = self._vectors.get(node.id) if qvec is not None else None
                if vec is not None:
                    cos = float(sum(a * b for a, b in zip(qvec, vec)))
                    relevance = max(0.0, min(1.0, (cos - self._COS_LO) / (self._COS_HI - self._COS_LO)))
                else:
                    relevance = _relevance_text_overlap(query, node.content)
                score = (
                    ALPHA_RECENCY * recency
                    + BETA_IMPORTANCE * importance
                    + GAMMA_RELEVANCE * relevance
                )
                results.append(RecallResult(
                    node=node,
                    score_total=score,
                    score_recency=recency,
                    score_importance=importance,
                    score_relevance=relevance,
                ))
            results.sort(key=lambda r: r.score_total, reverse=True)
            top = results[:top_k]
            # Mark accessed
            for r in top:
                r.node.last_accessed_ts = ts
                r.node.access_count += 1
        if top:
            logger.info(
                "Memory retrieve: query=%r returned %d (top score=%.3f)",
                query[:60], len(top), top[0].score_total,
            )
        return top

    def all_nodes(self) -> list[MemoryNode]:
        """Read-only view of all memories. For diagnostics."""
        with self._lock:
            return list(self._nodes)

    # ------------------------------------------------------------------
    # Memory ↔ body feedback
    # ------------------------------------------------------------------

    def apply_recall_feedback(
        self,
        node: MemoryNode,
        endocrine_event_callable,
        Event_class,
        now_ts: float | None = None,
    ) -> bool:
        """Fire small body event when memory is recalled.

        endocrine_event_callable: callable taking an Event (`get_endocrine().ingest_event`)
        Event_class: the Event dataclass (passed to avoid circular imports)

        Returns True if feedback fired, False if cooldown blocked it.

        Polarity:
          - High oxytocin at write → small oxytocin nudge on recall (warm memory effect)
          - High cortisol at write → small cortisol nudge (conflict memory aches)
          - High dopamine_phasic at write → small reward signal

        Anti-loop: each memory has its own cooldown (last_feedback_ts).
        """
        ts = now_ts or _now()
        with self._lock:
            if ts - node.last_feedback_ts < RECALL_COOLDOWN_SECONDS:
                return False
            node.last_feedback_ts = ts
            body = dict(node.body_state_at_write)

        # Derive valence + tags from the original body state
        oxy = body.get("oxytocin", 0.3)
        cort = body.get("cortisol", 0.3)
        dopa_p = body.get("dopamine_phasic", 0.0)

        # Compose a single "recall echo" event with scaled-down effect.
        # The body recalls in muted form — not as strong as the original moment.
        # skip_rpe=True because memory echoes are INTERNAL — they shouldn't
        # participate in TD-learning state transitions (see Phase 2B prediction
        # log M9 for the disappointment-inversion bug this prevents).
        scale = 0.3  # 30% of original effect
        if oxy > 0.5 and cort < 0.5:
            # Warm memory recall
            endocrine_event_callable(Event_class(
                type=f"memory_recall:warm",
                source="self_memory",
                valence=0.5 * scale,
                intensity=oxy * scale,
                tags=["affectionate", "memory_echo"],
                skip_rpe=True,
            ))
            return True
        if cort > 0.55:
            # Conflict/stress memory recall — small cortisol echo
            endocrine_event_callable(Event_class(
                type=f"memory_recall:tense",
                source="self_memory",
                valence=-0.4 * scale,
                intensity=cort * scale,
                tags=["memory_echo"],
                skip_rpe=True,
            ))
            return True
        if dopa_p > 0.2:
            # Excitement memory recall
            endocrine_event_callable(Event_class(
                type=f"memory_recall:reward",
                source="self_memory",
                valence=0.4 * scale,
                intensity=dopa_p * scale,
                tags=["memory_echo", "novelty"],
                skip_rpe=True,
            ))
            return True
        return False  # nothing notable to recall

    # ------------------------------------------------------------------
    # Persistence — append-only JSONL
    # ------------------------------------------------------------------

    def _append_to_disk(self, node: MemoryNode) -> None:
        try:
            self._stream_path.parent.mkdir(parents=True, exist_ok=True)
            with self._stream_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(node), ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("Memory disk-append failed: %s", exc)

    def _load(self) -> None:
        if not self._stream_path.exists():
            return
        loaded = 0
        skipped = 0
        try:
            with self._stream_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                        self._nodes.append(MemoryNode(**raw))
                        loaded += 1
                    except Exception:
                        # Corrupt line — skip, keep loading.
                        skipped += 1
            logger.info(
                "Memory loaded: %d nodes, %d corrupt lines skipped",
                loaded, skipped,
            )
        except Exception as exc:
            logger.warning("Memory load failed: %s", exc)


# ────────────────────────────────────────────────────────────────────
# Module-level singleton
# ────────────────────────────────────────────────────────────────────

_INSTANCE: MemoryStream | None = None
_INSTANCE_LOCK = threading.Lock()


def get_memory() -> MemoryStream:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = MemoryStream()
    return _INSTANCE
