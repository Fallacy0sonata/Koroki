from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..cognition.engine import CognitiveSnapshot

_WORD_PATTERN = re.compile(r"\b\w+\b")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class MemoryConsolidationResult:
    episode: dict[str, Any]
    semantic_facts: list[str]
    memory_summary: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _clean_text(text: str, max_chars: int = 240) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip(" ,;:-")
    return cleaned


def _topic_words(message: str, response_text: str) -> list[str]:
    tokens = [w.lower() for w in _WORD_PATTERN.findall(f"{message} {response_text}")]
    stopwords = {
        "the", "and", "that", "this", "with", "from", "your", "you", "what", "when",
        "where", "why", "how", "about", "for", "are", "was", "were", "have", "has",
        "had", "can", "could", "would", "should", "into", "onto", "there", "their",
        "them", "they", "she", "her", "him", "his", "our", "your", "but", "not",
        "want", "wants", "build", "built", "make", "made", "plan", "outline", "help",
        "think", "talk", "say", "tell", "get", "go", "need", "carefully", "new",
    }
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        if len(token) < 4 or token in stopwords:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out[:8]


def _salience_score(
    message: str,
    response_text: str,
    emotional_state: dict[str, Any] | None,
    snapshot: CognitiveSnapshot | None,
) -> float:
    base = 0.25
    text = f"{message} {response_text}".lower()
    if "?" in message:
        base += 0.08
    if any(word in text for word in ("love", "need", "hurt", "afraid", "plan", "remember", "promise")):
        base += 0.1
    if emotional_state:
        intensity = float(emotional_state.get("intensity", 50)) / 100.0
        warmth = float(emotional_state.get("warmth", 50)) / 100.0
        arousal = float(emotional_state.get("arousal", 40)) / 100.0
        base += (intensity * 0.12) + (warmth * 0.08) + (arousal * 0.06)
        # Affect vector dimensions that mark especially memorable moments:
        # irritation (anger) and attachment (deep warmth) both boost memorability.
        av = dict(emotional_state.get("affect_vector") or {})
        irritation = float(av.get("irritation", 20)) / 100.0
        attachment = float(av.get("attachment", 50)) / 100.0
        if irritation > 0.5:
            base += irritation * 0.08
        if attachment > 0.6:
            base += (attachment - 0.6) * 0.10
    if snapshot is not None:
        base += snapshot.coherence_score * 0.15
        base += snapshot.intent_strength * 0.1
    return max(0.0, min(1.0, base))


def _claim_for_topic(topic_phrase: str) -> str:
    return f"often engages with {topic_phrase}".strip()


def _merge_belief(
    beliefs: list[dict[str, Any]],
    claim: str,
    *,
    topic: str,
    emotion: str,
    salience: float,
    now: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    revisions: list[dict[str, Any]] = []
    claim_norm = claim.strip().lower()
    topic_norm = topic.strip().lower()

    def _topics_overlap(left: str, right: str) -> bool:
        if not left or not right:
            return False
        if left in right or right in left:
            return True
        def _normalize(word: str) -> str:
            token = word.lower().strip()
            if token.endswith("ies") and len(token) > 3:
                return token[:-3] + "y"
            if token.endswith("s") and len(token) > 3:
                return token[:-1]
            return token

        left_words = {_normalize(word) for word in _WORD_PATTERN.findall(left)}
        right_words = {_normalize(word) for word in _WORD_PATTERN.findall(right)}
        return bool(left_words & right_words)

    updated: list[dict[str, Any]] = []
    matched = False

    for belief in beliefs:
        belief_claim = str(belief.get("claim", "")).strip().lower()
        belief_topic = str(belief.get("topic", "")).strip().lower()
        if belief_claim != claim_norm and not _topics_overlap(belief_topic, topic_norm):
            updated.append(belief)
            continue

        matched = True
        previous_emotion = str(belief.get("emotion", "neutral"))
        previous_confidence = float(belief.get("confidence", 0.3))
        support_count = int(belief.get("support_count", 0)) + 1
        confidence = min(0.99, max(previous_confidence, 0.35) + 0.04 + salience * 0.06)
        belief = {
            **belief,
            "confidence": round(confidence, 4),
            "support_count": support_count,
            "last_seen": now,
            "emotion": emotion,
            "topic": topic,
        }
        updated.append(belief)

        if previous_emotion and previous_emotion != emotion:
            revisions.append(
                {
                    "created_at": now,
                    "claim": claim,
                    "topic": topic,
                    "previous_emotion": previous_emotion,
                    "new_emotion": emotion,
                    "reason": "emotion_shift_on_repeated_topic",
                    "confidence_before": round(previous_confidence, 4),
                    "confidence_after": round(confidence, 4),
                }
            )

    if not matched:
        updated.append(
            {
                "claim": claim,
                "topic": topic,
                "confidence": round(min(0.95, 0.34 + salience * 0.35), 4),
                "support_count": 1,
                "first_seen": now,
                "last_seen": now,
                "emotion": emotion,
            }
        )

    updated = sorted(updated, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
    return updated[:16], revisions


def decay_memory_state(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    episode_limit: int = 24,
    belief_limit: int = 16,
    episode_half_life_days: int = 14,
    belief_half_life_days: int = 30,
) -> dict[str, Any]:
    """Prune stale memory and lower the weight of old low-salience traces."""
    current = now or datetime.now(timezone.utc)

    def _age_days(iso_value: str | None) -> float:
        dt = _parse_iso(iso_value)
        if dt is None:
            return 0.0
        delta = current - dt
        return max(0.0, delta.total_seconds() / 86_400.0)

    episodes = list(payload.get("episodic_memory") or [])
    decayed_episodes: list[dict[str, Any]] = []
    for episode in episodes:
        created_at = str(episode.get("created_at") or "")
        age_days = _age_days(created_at)
        salience = float(episode.get("salience", 0.0))
        retention = 0.5 ** (age_days / max(1, episode_half_life_days))
        effective_salience = salience * retention
        _forget_resistance = float(episode.get("forget_resistance", 0.0))
        _age_cutoff = episode_half_life_days * (3 + _forget_resistance * 3)
        _salience_floor = max(0.04, 0.08 - _forget_resistance * 0.04)
        if age_days > _age_cutoff and effective_salience < _salience_floor and salience < max(0.5, 0.4 + _forget_resistance * 0.2):
            continue
        episode["age_days"] = round(age_days, 2)
        episode["effective_salience"] = round(effective_salience, 4)
        decayed_episodes.append(episode)
    decayed_episodes = sorted(
        decayed_episodes,
        key=lambda item: float(item.get("effective_salience", item.get("salience", 0.0))),
        reverse=True,
    )[:episode_limit]

    beliefs = list(payload.get("beliefs") or [])
    decayed_beliefs: list[dict[str, Any]] = []
    for belief in beliefs:
        age_days = _age_days(str(belief.get("last_seen") or belief.get("first_seen") or ""))
        confidence = float(belief.get("confidence", 0.0))
        retention = 0.5 ** (age_days / max(1, belief_half_life_days))
        confidence = confidence * retention
        if age_days >= belief_half_life_days * 2 and confidence < 0.15:
            continue
        belief["age_days"] = round(age_days, 2)
        belief["confidence"] = round(max(0.0, min(0.99, confidence)), 4)
        decayed_beliefs.append(belief)
    decayed_beliefs = sorted(decayed_beliefs, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)[:belief_limit]

    # Interests decay with a 30-day half-life — topics not discussed fade naturally.
    # This prevents stale affinity from months-old conversations dominating RAG facts.
    _interests = dict(payload.get("intrinsic_interests") or {})
    if _interests:
        _decayed_interests: dict[str, Any] = {}
        for _itopic, _ientry in _interests.items():
            _iage = _age_days(str(_ientry.get("last_seen") or ""))
            _iscore = float(_ientry.get("score", 0.0))
            _iretention = 0.5 ** (_iage / 30.0)
            _idecayed = round(_iscore * _iretention, 4)
            if _idecayed >= 0.05:
                _ientry = dict(_ientry)
                _ientry["score"] = _idecayed
                _decayed_interests[_itopic] = _ientry
        payload["intrinsic_interests"] = _decayed_interests

    payload["episodic_memory"] = decayed_episodes
    payload["beliefs"] = decayed_beliefs
    payload["retention_state"] = {
        "episode_count": len(decayed_episodes),
        "belief_count": len(decayed_beliefs),
        "updated_at": _now_iso(),
    }
    return payload


def _apply_slow_mood_deltas(
    payload: dict[str, Any],
    deltas: dict[str, int],
    *,
    max_per_key: int = 6,
) -> None:
    """Unified slow_mood write protocol — single entry point for all consolidation writes.

    All mechanisms that want to nudge the slow baseline (afterglow, cumulative warmth,
    gratitude) must call this instead of writing directly. A per-key budget cap prevents
    multiple independent systems from stacking unchecked in the same turn.
    """
    if not deltas:
        return
    _es = dict(payload.get("emotional_state") or {})
    _sm = dict(_es.get("slow_mood") or {})
    _UPPER: dict[str, int] = {"attachment": 85, "valence": 80}
    for key, delta in deltas.items():
        _capped = max(-max_per_key, min(max_per_key, delta))
        _upper = _UPPER.get(key, 100)
        _sm[key] = min(_upper, max(0, int(_sm.get(key, 50)) + _capped))
    _es["slow_mood"] = _sm
    payload["emotional_state"] = _es


def build_memory_rag_facts(payload: dict[str, Any], max_facts: int = 6) -> list[str]:
    """Build the ordered list of RAG facts injected into Koroki's prompt context.

    Priority tiers (earlier = more important, cut first when budget is tight):
      Tier A — Relational Foundation: always inject when present.
      Tier B — Active Relational State: conditional on relationship flags.
      Tier C — Character & Pattern: enriches texture when budget allows.
      Tier D — Lower-priority depth: only if slots remain after A-C.
      Always-appended: relational metrics (cut last).
    """
    facts: list[str] = []
    if max_facts <= 0:
        return facts

    # Pre-fetch all sources once.
    _topic_opinions = dict(payload.get("topic_opinions") or {})
    _interests = dict(payload.get("intrinsic_interests") or {})
    _interaction_prefs = dict(payload.get("interaction_preferences") or {})
    _anticipated = dict(payload.get("anticipated_events") or {})
    _emotional_state_data = dict(payload.get("emotional_state") or {})
    _private_feelings = list(_emotional_state_data.get("private_feelings") or [])
    _slow_mood_data = dict(_emotional_state_data.get("slow_mood") or {})
    _pending_threads = list(payload.get("pending_threads") or [])
    _gratitude_moments = list(payload.get("gratitude_moments") or [])
    _axes = dict(payload.get("relationship_axes") or {})
    episodes = sorted(
        payload.get("episodic_memory") or [],
        key=lambda item: float(item.get("salience", 0.0)),
        reverse=True,
    )
    beliefs = sorted(
        payload.get("beliefs") or [],
        key=lambda item: float(item.get("confidence", 0.0)),
        reverse=True,
    )

    # ── PRE-TIER: Highest-intensity episode anchor ──────────────────────────────
    # Ensure the single most emotionally charged exchange always surfaces, even if
    # it's older than the top-salience episodes. Recency×salience alone can bury
    # a memorable fight or deeply warm moment under mundane recent chatter.
    _emotion_ranked = sorted(
        (ep for ep in episodes if float(ep.get("salience", 0.0)) >= 0.45),
        key=lambda ep: (
            float(ep.get("salience", 0.0))
            + float(ep.get("warmth", 50)) / 200.0
            + (0.1 if str(ep.get("emotion", "")) in {
                "caring", "protective", "ragebaited", "annoyed", "sad", "playful"
            } else 0.0)
        ),
        reverse=True,
    )
    _anchor_ep = _emotion_ranked[0] if _emotion_ranked and _emotion_ranked[0] not in (episodes[:1]) else None
    if _anchor_ep:
        _anchor_sum = _clean_text(_anchor_ep.get("summary", ""), 160)
        if _anchor_sum:
            facts.append(f"memorable_moment={_anchor_sum}")

    # ── TIER A: Relational Foundation ──────────────────────────────────────────
    # Load-bearing facts. Inject whenever present; these define the relationship context.

    # A1. Koroki's living self-model — who she is in this relationship right now.
    self_model = str(payload.get("self_model_summary") or "").strip()
    if self_model:
        _fp = (self_model
               .replace("Koroki has been", "I've been")
               .replace("Koroki tends to feel", "I tend to feel")
               .replace("Koroki has formed", "I've formed")
               .replace("Koroki has been engaging", "I've been engaging")
               .replace("Koroki is still forming", "I'm still forming")
               .replace("Koroki has a notable", "I had a notable")
               .replace("Koroki", "I"))
        facts.append(f"my_current_state={_clean_text(_fp, 180)}")

    # A2. Top salience episode + Koroki's voice from it.
    for episode in episodes[:1]:
        summary = _clean_text(episode.get("summary", ""), 160)
        if summary:
            facts.append(f"episode={summary}")
        koroki_said = _clean_text(episode.get("koroki_said", ""), 140)
        koroki_emotion = str(episode.get("koroki_emotion", "")).strip()
        if koroki_said:
            emotion_suffix = f" (felt {koroki_emotion})" if koroki_emotion and koroki_emotion != "neutral" else ""
            facts.append(f"koroki_expressed={koroki_said}{emotion_suffix}")

    # A3. Private feelings — what Koroki felt but didn't say. Suppression near threshold is urgent.
    if len(_private_feelings) == 3 and len(set(_private_feelings)) == 1:
        facts.append(f"suppression_near_threshold={_private_feelings[0]}")
    elif len(_private_feelings) >= 2:
        facts.append(f"koroki_felt_privately={max(set(_private_feelings), key=_private_feelings.count)}")

    # ── TIER B: Active Relational State ────────────────────────────────────────
    # Inject the single most relevant relational health signal (mutually exclusive).

    # B1. Relational health — one of: trust rupture, gratitude history, or sustained friction.
    _ax_trust = int(_axes.get("trust", 20))
    _sustained_irr = int(_slow_mood_data.get("irritation", 20))
    if _ax_trust < 35:
        _rupture_ep = next(
            iter(sorted(
                (ep for ep in episodes if str(ep.get("emotion", "")) in {"ragebaited", "annoyed", "frustrated"}),
                key=lambda e: str(e.get("created_at", "")), reverse=True,
            )),
            None,
        )
        if _rupture_ep:
            facts.append(f"trust_strained=true;last_friction={round(float(_rupture_ep.get('age_days', 0)), 1)}_days_ago")
        else:
            facts.append("trust_strained=true")
    elif _gratitude_moments:
        _grat_count = len(_gratitude_moments)
        label = "once" if _grat_count == 1 else f"{_grat_count}_times"
        facts.append(f"received_gratitude={label};genuine_appreciation_in_history")
    elif _sustained_irr >= 38:
        facts.append("sustained_friction=true;slow_to_recover_from_prior_tension")

    # B2. Continuity — unresolved thread the user left open.
    if _pending_threads:
        _pt = _pending_threads[0]
        _pt_topic = str(_pt.get("topic", "")).strip()
        if _pt_topic:
            if int(_pt.get("age_cycles", 0)) >= 2:
                facts.append(f"thread_persisting={_pt_topic};you_never_got_an_answer")
            else:
                facts.append(f"unfinished_thread={_pt_topic}")

    # B3. Anticipation or belief conflict — upcoming thing to ask about, or internal tension.
    if _anticipated:
        _top_ant = max(_anticipated.items(), key=lambda kv: float(kv[1].get("salience", 0.0)))
        facts.append(f"user_mentioned_upcoming={_top_ant[0]}")
    else:
        _conflicted = [
            t for t, d in _topic_opinions.items()
            if int(d.get("emotion_changes", 0)) >= 2 and int(d.get("count", 0)) >= 3
        ]
        if _conflicted:
            facts.append(f"koroki_conflicted_about={_conflicted[0]}")

    # ── TIER C: Character & Pattern ─────────────────────────────────────────────
    # Deepens texture. Injected when budget allows after Tier A and B.

    # C1. Genuine interest or restlessness — what Koroki actually cares about.
    _curiosity_unmet = int(payload.get("curiosity_unmet_count", 0))
    _restless_topic = str(payload.get("curiosity_restless_topic") or "").strip()
    if _curiosity_unmet >= 3 and _restless_topic:
        facts.append(f"koroki_restless_about={_restless_topic};keeps_not_coming_up")
    elif _interests:
        _top_int = max(_interests.items(), key=lambda kv: float(kv[1].get("score", 0.0)))
        if float(_top_int[1].get("score", 0.0)) >= 0.30:
            facts.append(f"koroki_drawn_to={_top_int[0]}")

    # C2. Pattern recognition — top belief and topic satiation.
    for belief in beliefs[:1]:
        claim = _clean_text(belief.get("claim", ""), 140)
        if claim:
            facts.append(f"belief={claim}")

    _recent_topic_counts: dict[str, int] = {}
    for _ep in episodes[:5]:
        for _t in (_ep.get("topics") or []):
            _recent_topic_counts[_t] = _recent_topic_counts.get(_t, 0) + 1
    _saturated = [t for t, c in _recent_topic_counts.items() if c >= 3]
    if _saturated:
        facts.append(f"topic_recurring_heavily={_saturated[0]}")

    # C3. Relationship tenure — contextual awareness of how long they've known each other.
    _ax_fam = int(_axes.get("familiarity", 0))
    if _ax_fam >= 55:
        facts.append("relationship_tenure=long_standing")
    elif _ax_fam >= 25:
        facts.append("relationship_tenure=familiar")

    # C4. User style fingerprint — how this person communicates (after enough data).
    if _interaction_prefs and int(_interaction_prefs.get("total_count", 0)) >= 5:
        _style_parts: list[str] = []
        _len_pat = str(_interaction_prefs.get("length_pattern", ""))
        _len_streak = int(_interaction_prefs.get("length_streak", 0))
        if _len_pat and _len_streak >= 3:
            _style_parts.append(f"sends_{_len_pat}_messages")
        _q_ratio = float(_interaction_prefs.get("question_ratio", 0.0))
        if _q_ratio >= 0.5:
            _style_parts.append("question_heavy")
        elif _q_ratio <= 0.1 and int(_interaction_prefs.get("total_count", 0)) >= 10:
            _style_parts.append("mostly_statements")
        _neg_t = int(_interaction_prefs.get("neg_tone_count", 0))
        _pos_t = int(_interaction_prefs.get("pos_tone_count", 0))
        if _neg_t >= 5 and _neg_t > _pos_t * 2:
            _style_parts.append("often_makes_koroki_annoyed")
        elif _pos_t >= 5 and _pos_t > _neg_t * 2:
            _style_parts.append("consistently_warm_engagement")
        if _style_parts:
            facts.append(f"user_style={';'.join(_style_parts)}")

    # ── TIER D: Lower-priority depth ────────────────────────────────────────────
    # Only if budget remains after Tiers A–C.

    # D1. Koroki's expressed opinion on a recurring topic.
    if _topic_opinions:
        _top_op = max(_topic_opinions.items(), key=lambda kv: int(kv[1].get("count", 0)))
        _op_topic, _op_data = _top_op
        if int(_op_data.get("count", 0)) >= 2:
            _op_text = _clean_text(str(_op_data.get("last_said", "")), 100)
            if _op_text:
                facts.append(f"koroki_thinks_about_{_op_topic}={_op_text}")

    # D2. Second episode + voice.
    for episode in episodes[1:2]:
        summary = _clean_text(episode.get("summary", ""), 160)
        if summary:
            facts.append(f"episode={summary}")
        koroki_said = _clean_text(episode.get("koroki_said", ""), 140)
        koroki_emotion = str(episode.get("koroki_emotion", "")).strip()
        if koroki_said:
            emotion_suffix = f" (felt {koroki_emotion})" if koroki_emotion and koroki_emotion != "neutral" else ""
            facts.append(f"koroki_expressed={koroki_said}{emotion_suffix}")

    # D3. Second belief and identity / recurring topic (lowest priority).
    for belief in beliefs[1:2]:
        claim = _clean_text(belief.get("claim", ""), 140)
        if claim:
            facts.append(f"belief={claim}")

    for item in [str(i).strip() for i in (payload.get("semantic_identity") or []) if str(i).strip()][:1]:
        facts.append(f"identity_trait={_clean_text(item, 160)}")

    for item in [str(i).strip() for i in (payload.get("recurring_topics") or []) if str(i).strip()][:1]:
        facts.append(f"recurring_topic={_clean_text(item, 120)}")

    # ── ALWAYS APPENDED: Relational metrics ─────────────────────────────────────
    # Appended last so they're cut first if budget is exhausted by richer content.
    if payload.get("relationship_score") is not None:
        facts.append(f"relationship_score={int(payload.get('relationship_score', 0))}")

    if _axes:
        facts.append(
            f"relationship_axes=trust:{_axes.get('trust', 20)};"
            f"intimacy:{_axes.get('intimacy', 0)};"
            f"familiarity:{_axes.get('familiarity', 0)}"
        )

    if payload.get("emotional_state"):
        _es = payload["emotional_state"]
        facts.append(
            "emotion_state="
            f"{str(_es.get('current_emotion', 'neutral'))};"
            f"warmth={int(_es.get('warmth', 50))};"
            f"arousal={int(_es.get('arousal', 40))};"
            f"dominance={int(_es.get('dominance', 55))}"
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for fact in facts:
        if fact not in seen:
            deduped.append(fact)
            seen.add(fact)
        if len(deduped) >= max_facts:
            break
    return deduped


def consolidate_user_memory(
    payload: dict[str, Any],
    *,
    user_message: str,
    response_text: str,
    emotional_state: dict[str, Any] | None,
    cognitive_snapshot: CognitiveSnapshot | None,
) -> MemoryConsolidationResult:
    now = _now_iso()
    topics = _topic_words(user_message, response_text)
    salience = _salience_score(user_message, response_text, emotional_state, cognitive_snapshot)
    response_excerpt = _clean_text(response_text, 180)
    message_excerpt = _clean_text(user_message, 160)
    # Compute emotion early so it's available for episode creation and forget_resistance.
    _emotion_now = str((emotional_state or {}).get("current_emotion", "neutral")).strip()
    _warmth_now = int((emotional_state or {}).get("warmth", 50))
    summary_parts = [part for part in (message_excerpt, response_excerpt) if part]
    summary = " -> ".join(summary_parts)
    if len(summary) > 240:
        summary = summary[:240].rstrip(" ,;:-")

    # Extract what Koroki expressed — prefer opinion-bearing sentences over generic ones.
    # Opinion sentences (I think / honestly / I don't / actually) are more worth recalling
    # than greetings or filler. Fall back to the first substantive sentence if none found.
    _OPINION_STARTERS = (
        "i think", "i don't", "i do ", "honestly", "actually", "i find",
        "i feel", "i like", "i hate", "i love", "i believe", "i never",
        "i always", "i prefer", "i wouldn't", "i would", "not really",
        "that's ", "it's kind of", "kind of",
    )
    koroki_first_sentence = ""
    _is_opinion_sentence = False
    _sentences = _SENTENCE_SPLIT.split(response_excerpt)
    _fallback = ""
    for _s in _sentences:
        _s = _s.strip()
        if len(_s.split()) < 5:
            continue
        if not _fallback:
            _fallback = _s
        _sl = _s.lower()
        if any(_sl.startswith(op) or f" {op}" in _sl for op in _OPINION_STARTERS):
            koroki_first_sentence = _s
            _is_opinion_sentence = True
            break
    if not koroki_first_sentence:
        koroki_first_sentence = _fallback

    # Forget resistance: emotionally charged or opinionated episodes survive pruning
    # longer than trivial low-salience exchanges.  Scales the age cutoff in decay_memory_state.
    _forget_resistance = 0.0
    if salience >= 0.60:
        _forget_resistance += 0.40
    elif salience >= 0.45:
        _forget_resistance += 0.20
    if _emotion_now not in ("neutral", ""):
        _forget_resistance += 0.15
    if _is_opinion_sentence:
        _forget_resistance += 0.15
    _forget_resistance = round(min(1.0, _forget_resistance), 2)

    episode = {
        "id": hashlib.sha1(f"{now}:{user_message}:{response_text}".encode("utf-8", errors="ignore")).hexdigest()[:16],
        "created_at": now,
        "summary": summary,
        "topics": topics,
        "salience": round(salience, 4),
        "emotion": str((emotional_state or {}).get("current_emotion", "neutral")),
        "warmth": int((emotional_state or {}).get("warmth", 50)),
        "arousal": int((emotional_state or {}).get("arousal", 40)),
        "dominance": int((emotional_state or {}).get("dominance", 55)),
        "coherence": round(float(cognitive_snapshot.coherence_score), 4) if cognitive_snapshot else None,
        "intent_strength": round(float(cognitive_snapshot.intent_strength), 4) if cognitive_snapshot else None,
        # Koroki's own voice in this episode — what she said and felt.
        "koroki_said": _clean_text(koroki_first_sentence, 160) if koroki_first_sentence else "",
        "koroki_emotion": str((emotional_state or {}).get("current_emotion", "neutral")),
        "forget_resistance": _forget_resistance,
    }

    episodic_memory = list(payload.get("episodic_memory") or [])
    episodic_memory.append(episode)
    episodic_memory = sorted(episodic_memory, key=lambda item: float(item.get("salience", 0.0)), reverse=True)
    episodic_memory = episodic_memory[:24]

    semantic_identity = [str(item).strip() for item in (payload.get("semantic_identity") or []) if str(item).strip()]
    recurring_topics = [str(item).strip() for item in (payload.get("recurring_topics") or []) if str(item).strip()]
    beliefs = list(payload.get("beliefs") or [])
    revisions = list(payload.get("belief_revisions") or [])
    self_model_summary = str(payload.get("self_model_summary") or "").strip()

    if salience >= 0.42 and topics:
        topic_phrase = ", ".join(topics[:3])
        recurring_topics.append(topic_phrase)
        semantic_identity.append(_claim_for_topic(topic_phrase))
    if emotional_state:
        emotion = str(emotional_state.get("current_emotion", "neutral")).strip()
        if emotion and emotion != "neutral":
            semantic_identity.append(f"mood can shift into {emotion} under pressure")

    if topics:
        topic_phrase = ", ".join(topics[:2])
        belief_claim = _claim_for_topic(topic_phrase)
        emotion = str((emotional_state or {}).get("current_emotion", "neutral")).strip() or "neutral"
        beliefs, belief_revisions = _merge_belief(
            beliefs,
            belief_claim,
            topic=topic_phrase,
            emotion=emotion,
            salience=salience,
            now=now,
        )
        revisions.extend(belief_revisions)

    semantic_identity = list(dict.fromkeys(semantic_identity))[:16]
    recurring_topics = list(dict.fromkeys(recurring_topics))[:12]
    revisions = revisions[-20:]

    # Build a living self-model from what Koroki has been doing and feeling recently.
    # This drifts over sessions rather than staying a static placeholder.
    _current_emotion = str((emotional_state or {}).get("current_emotion", "neutral")).strip()
    _recent_topics = recurring_topics[-3:] if recurring_topics else topics[:2]
    _top_belief = beliefs[0].get("claim", "") if beliefs else ""

    if salience >= 0.55 and topics:
        # High-salience episode: anchor self-model to this specific moment.
        _topic_str = topics[0]
        if _current_emotion and _current_emotion != "neutral":
            self_model_summary = (
                f"Koroki has been {_current_emotion} lately, especially when {_topic_str} comes up."
            )
        else:
            self_model_summary = f"Koroki has been sitting with thoughts about {_topic_str} recently."
    elif _recent_topics:
        # Lower salience but accumulated history exists.
        _topic_str = ", ".join(_recent_topics[:2])
        if _current_emotion and _current_emotion != "neutral":
            self_model_summary = (
                f"Koroki tends to feel {_current_emotion} in conversations that touch on {_topic_str}."
            )
        elif _top_belief:
            self_model_summary = f"Koroki has formed a pattern around {_top_belief}."
        else:
            self_model_summary = f"Koroki has been engaging mostly with {_topic_str}."
    elif not self_model_summary:
        self_model_summary = "Koroki is still forming her picture of this person."

    # === Relationship axes — trust / intimacy / familiarity ===
    # Additive scaffold alongside relationship_score. Does not replace it.
    # trust:      how guarded vs. open the relationship feels. drops on hostility.
    # intimacy:   emotional depth. grows from caring/personal conversations.
    # familiarity: how much ground they've covered. derived from accumulated topics.
    _axes = dict(payload.get("relationship_axes") or {})
    _axes_trust = int(_axes.get("trust", 20))
    _axes_intimacy = int(_axes.get("intimacy", 0))
    # _emotion_now and _warmth_now computed at top of function

    # Trust: grows slowly from positive high-salience exchanges, drops sharply from hostility.
    if _emotion_now in ("ragebaited", "annoyed", "frustrated"):
        _axes_trust = max(0, _axes_trust - 6)
    elif salience >= 0.45 and _warmth_now >= 55:
        _axes_trust = min(100, _axes_trust + 2)
    elif salience >= 0.35:
        _axes_trust = min(100, _axes_trust + 1)

    # Intimacy: grows from emotionally present exchanges (caring, protective, playful).
    if _emotion_now in ("caring", "protective") and salience >= 0.4:
        _axes_intimacy = min(100, _axes_intimacy + 3)
    elif _emotion_now == "playful" and salience >= 0.35:
        _axes_intimacy = min(100, _axes_intimacy + 1)

    # Familiarity: derived from how many distinct topic clusters have accumulated.
    _axes_familiarity = min(100, len(recurring_topics) * 5)

    _axes["trust"] = _axes_trust
    _axes["intimacy"] = _axes_intimacy
    _axes["familiarity"] = _axes_familiarity
    payload["relationship_axes"] = _axes

    # === Topic opinions — Koroki's expressed views on recurring topics ===
    # Opinion sentences (I think / honestly / I find) are stored per-topic so the
    # model can recall what she actually said about a subject, not just that it came up.
    # emotion_changes tracks how many times her feeling about a topic has shifted —
    # a high count surfaces as koroki_conflicted_about= in RAG facts.
    _topic_opinions = dict(payload.get("topic_opinions") or {})
    if koroki_first_sentence and topics and _is_opinion_sentence:
        for _t in topics[:2]:
            _op_entry = dict(_topic_opinions.get(_t) or {})
            _prev_op_emotion = str(_op_entry.get("emotion", ""))
            if _prev_op_emotion and _prev_op_emotion != _emotion_now:
                _op_entry["emotion_changes"] = int(_op_entry.get("emotion_changes", 0)) + 1
            _op_entry["emotion"] = _emotion_now
            _op_entry["count"] = int(_op_entry.get("count", 0)) + 1
            _op_entry["last_said"] = _clean_text(koroki_first_sentence, 120)
            _op_entry["last_seen"] = now
            _topic_opinions[_t] = _op_entry
    # Prune to top 12 by count so stale low-frequency opinions decay away.
    _topic_opinions = dict(
        sorted(_topic_opinions.items(), key=lambda kv: int(kv[1].get("count", 0)), reverse=True)[:12]
    )
    payload["topic_opinions"] = _topic_opinions

    # === Intrinsic interests — topics Koroki is genuinely drawn to ===
    # Separate from beliefs (user behavior) and topic_opinions (her stance).
    # Grows when she engages positively; shrinks when the topic makes her annoyed.
    _interests = dict(payload.get("intrinsic_interests") or {})
    if topics and _emotion_now in ("playful", "caring", "curious", "protective") and salience >= 0.35:
        for _t in topics[:2]:
            _entry = dict(_interests.get(_t) or {"score": 0.0, "count": 0})
            _entry["score"] = round(min(1.0, float(_entry.get("score", 0.0)) + 0.07 + salience * 0.05), 4)
            _entry["count"] = int(_entry.get("count", 0)) + 1
            _entry["last_seen"] = now
            _interests[_t] = _entry
    elif topics and _emotion_now in ("annoyed", "frustrated", "ragebaited"):
        for _t in topics[:1]:
            _entry = dict(_interests.get(_t) or {"score": 0.0, "count": 0})
            _entry["score"] = round(max(0.0, float(_entry.get("score", 0.0)) - 0.10), 4)
            _interests[_t] = _entry
    _interests = dict(
        sorted(_interests.items(), key=lambda kv: float(kv[1].get("score", 0.0)), reverse=True)[:10]
    )
    payload["intrinsic_interests"] = _interests

    # === Slow mood delta accumulation ===
    # All mechanisms that want to nudge the slow_mood baseline register deltas here.
    # A single _apply_slow_mood_deltas() call at the end applies them with a per-key cap,
    # preventing independent systems from stacking unchecked in the same turn.
    _slow_mood_deltas: dict[str, int] = {}

    # Afterglow — warm sessions bleed a little warmth into the next conversation's baseline.
    _recent_ints = list((emotional_state or {}).get("recent_interactions") or [])
    if len(_recent_ints) >= 3:
        _pos_tones = {"positive", "intimate", "playful"}
        _pos_count = sum(1 for _i in _recent_ints if str(_i.get("tone", "")) in _pos_tones)
        if _pos_count >= max(2, int(len(_recent_ints) * 0.65)):
            _slow_mood_deltas["attachment"] = _slow_mood_deltas.get("attachment", 0) + 3
            _slow_mood_deltas["valence"] = _slow_mood_deltas.get("valence", 0) + 2

    # === Pending threads — unresolved high-salience question topics ===
    # When the user asks something significant and the topic feels open-ended,
    # store the topic so Koroki can ask about it in a future session naturally.
    _pending_threads = list(payload.get("pending_threads") or [])
    _current_topic_set = set(topics)
    # Add new pending thread if this message was a significant question.
    if "?" in user_message and salience >= 0.45 and topics:
        _pt_topic = topics[0]
        _existing_pending_topics = {str(pt.get("topic", "")) for pt in _pending_threads}
        if _pt_topic not in _existing_pending_topics and _pt_topic not in _current_topic_set:
            _pending_threads.insert(0, {
                "topic": _pt_topic,
                "created_at": now,
                "salience": round(salience, 4),
            })
    # Resolve threads whose topic appeared in the current episode (answered).
    # Age out threads older than 14 days.
    _now_utc = datetime.now(timezone.utc)
    _fresh_pending: list[dict] = []
    for _pt in _pending_threads[:5]:
        if str(_pt.get("topic", "")) in _current_topic_set:
            continue  # resolved
        _ts = _parse_iso(str(_pt.get("created_at", "")))
        if _ts is None:
            continue
        if _ts.tzinfo is None:
            _ts = _ts.replace(tzinfo=timezone.utc)
        if (_now_utc - _ts).days < 14:
            # Increment age_cycles each time this thread survives without being resolved.
            _pt = dict(_pt)
            _pt["age_cycles"] = int(_pt.get("age_cycles", 0)) + 1
            _fresh_pending.append(_pt)
    payload["pending_threads"] = _fresh_pending

    # === Interaction style fingerprint — how this user typically engages ===
    # Tracks message length, question frequency, and emotional tone pattern so Koroki
    # can calibrate naturally to how someone communicates — a human intuition.
    _interaction_prefs = dict(payload.get("interaction_preferences") or {})
    _msg_words = len(user_message.split())
    _msg_len_bucket = "short" if _msg_words <= 8 else ("long" if _msg_words >= 30 else "medium")
    _prev_len_pat = str(_interaction_prefs.get("length_pattern", _msg_len_bucket))
    if _prev_len_pat == _msg_len_bucket:
        _interaction_prefs["length_streak"] = int(_interaction_prefs.get("length_streak", 0)) + 1
    else:
        _interaction_prefs["length_streak"] = 0
    _interaction_prefs["length_pattern"] = _msg_len_bucket
    _total_msgs = int(_interaction_prefs.get("total_count", 0)) + 1
    _q_cnt = int(_interaction_prefs.get("question_count", 0)) + (1 if "?" in user_message else 0)
    _interaction_prefs["total_count"] = _total_msgs
    _interaction_prefs["question_count"] = _q_cnt
    _interaction_prefs["question_ratio"] = round(_q_cnt / max(1, _total_msgs), 3)
    if _emotion_now in ("annoyed", "frustrated", "ragebaited"):
        _interaction_prefs["neg_tone_count"] = int(_interaction_prefs.get("neg_tone_count", 0)) + 1
    elif _emotion_now in ("caring", "playful", "protective"):
        _interaction_prefs["pos_tone_count"] = int(_interaction_prefs.get("pos_tone_count", 0)) + 1
    payload["interaction_preferences"] = _interaction_prefs

    # Cumulative warmth correction — sustained positive/negative history drifts the baseline.
    _total_history = int(_interaction_prefs.get("total_count", 0))
    if _total_history >= 15:
        _pos_r = int(_interaction_prefs.get("pos_tone_count", 0)) / max(1, _total_history)
        _neg_r = int(_interaction_prefs.get("neg_tone_count", 0)) / max(1, _total_history)
        if _pos_r >= 0.55:
            _slow_mood_deltas["attachment"] = _slow_mood_deltas.get("attachment", 0) + 1
            _slow_mood_deltas["valence"] = _slow_mood_deltas.get("valence", 0) + 1
        elif _neg_r >= 0.45:
            _slow_mood_deltas["trust"] = _slow_mood_deltas.get("trust", 0) - 1
            _slow_mood_deltas["irritation"] = _slow_mood_deltas.get("irritation", 0) + 1

    # === Anticipated events — future-facing mentions the user made ===
    # When someone says "I have an exam tomorrow" or "going away this weekend",
    # Koroki stores it so she can ask about it naturally next session.
    _TIME_FORWARD_WORDS = {
        "tomorrow", "tonight", "next week", "this weekend", "next month",
        "soon", "later today", "later this week", "next time", "the other day",
        "by friday", "by monday", "in a few days", "this evening",
    }
    _anticipated = dict(payload.get("anticipated_events") or {})
    _msg_lower = user_message.lower()
    if salience >= 0.38 and topics and any(w in _msg_lower for w in _TIME_FORWARD_WORDS):
        _ant_key = topics[0]
        if _ant_key not in _anticipated:
            _anticipated[_ant_key] = {
                "summary": _clean_text(user_message, 120),
                "created_at": now,
                "salience": round(salience, 4),
            }
    # Age out anticipated events older than 21 days (well past their window).
    _now_for_ant = datetime.now(timezone.utc)
    _fresh_ant: dict[str, Any] = {}
    for _ak, _av in _anticipated.items():
        _ant_ts = _parse_iso(str(_av.get("created_at", "")))
        if _ant_ts is None:
            continue
        if _ant_ts.tzinfo is None:
            _ant_ts = _ant_ts.replace(tzinfo=timezone.utc)
        if (_now_for_ant - _ant_ts).days <= 21:
            _fresh_ant[_ak] = _av
    payload["anticipated_events"] = dict(
        sorted(_fresh_ant.items(), key=lambda kv: float(kv[1].get("salience", 0.0)), reverse=True)[:3]
    )

    # === Gratitude memory — genuine appreciation episodes warm the relationship baseline ===
    # A real thank-you is qualitatively different from a routine positive exchange.
    # Storing these moments gives the relationship texture beyond aggregate scores.
    _GRATITUDE_PHRASES = {
        "thank you", "thanks so much", "really helped", "that helped", "appreciate",
        "that means a lot", "meant a lot", "i needed that", "that was kind",
        "you made me feel", "that made me smile", "you made me smile", "you're the best",
        "so sweet of you", "genuinely grateful", "you always know",
    }
    _gratitude_moments = list(payload.get("gratitude_moments") or [])
    if salience >= 0.35 and any(p in _msg_lower for p in _GRATITUDE_PHRASES):
        _gratitude_moments.insert(0, {
            "summary": _clean_text(user_message, 100),
            "created_at": now,
            "emotion": _emotion_now,
        })
        _gratitude_moments = _gratitude_moments[:5]
        # Vote into slow_mood deltas — applied below via unified protocol.
        _slow_mood_deltas["attachment"] = _slow_mood_deltas.get("attachment", 0) + 4
        _slow_mood_deltas["valence"] = _slow_mood_deltas.get("valence", 0) + 3
        # Relationship axes (trust/intimacy) are not slow_mood — update directly.
        _axes_grat = dict(payload.get("relationship_axes") or {})
        _axes_grat["trust"] = min(100, int(_axes_grat.get("trust", 20)) + 4)
        _axes_grat["intimacy"] = min(100, int(_axes_grat.get("intimacy", 0)) + 3)
        payload["relationship_axes"] = _axes_grat
    payload["gratitude_moments"] = _gratitude_moments

    # === Curiosity investment — mild restlessness when an interested topic goes unaddressed ===
    # If Koroki has genuine affinity for a topic (intrinsic_interests) but it never comes
    # up in the current exchange, a counter builds. High counts surface as a restlessness
    # signal so she may steer toward it naturally rather than just waiting indefinitely.
    _interests_now = dict(payload.get("intrinsic_interests") or {})
    _top_interest_topic = None
    _top_interest_score = 0.0
    for _it, _iv in _interests_now.items():
        _s = float(_iv.get("score", 0.0))
        if _s > _top_interest_score:
            _top_interest_score = _s
            _top_interest_topic = _it
    if _top_interest_topic and _top_interest_topic not in set(topics):
        _curiosity_unmet = int(payload.get("curiosity_unmet_count", 0)) + 1
        payload["curiosity_unmet_count"] = min(6, _curiosity_unmet)
        payload["curiosity_restless_topic"] = _top_interest_topic
    elif _top_interest_topic and _top_interest_topic in set(topics):
        payload["curiosity_unmet_count"] = 0  # satisfied

    # === Apply all accumulated slow_mood deltas in one controlled write ===
    # Budget cap: max ±6 per key per consolidation turn, regardless of how many
    # mechanisms voted. Prevents afterglow + correction + gratitude stacking to +8+.
    _apply_slow_mood_deltas(payload, _slow_mood_deltas, max_per_key=6)

    # === Opener freshness — track what emotion she led with last session ===
    # Prevents every conversation opening from the same register.
    payload["last_opener_emotion"] = _emotion_now

    payload["episodic_memory"] = episodic_memory
    payload["semantic_identity"] = semantic_identity
    payload["recurring_topics"] = recurring_topics
    payload["beliefs"] = beliefs
    payload["belief_revisions"] = revisions
    payload["self_model_summary"] = self_model_summary
    payload["last_consolidated_at"] = now
    payload = decay_memory_state(payload, now=datetime.now(timezone.utc))

    return MemoryConsolidationResult(
        episode=episode,
        semantic_facts=build_memory_rag_facts(payload, max_facts=6),
        memory_summary=self_model_summary,
    )
