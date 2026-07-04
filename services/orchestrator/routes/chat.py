"""
Non-streaming chat endpoint.

Suitable for Discord message replies where a complete response is needed
before sending. Calls Brain → Guillotine → TTS in sequence, returns
text + audio availability in one JSON response.
"""

import asyncio
import base64
import copy
import json
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import httpx
import websockets
from fastapi import APIRouter, HTTPException

from ..memory.cache import user_memory_cache
from ..memory.intelligence import build_memory_rag_facts, consolidate_user_memory, decay_memory_state
from ..pipeline.slots import create_inference_slot
from ..schemas import ChatRequest
from ..telemetry.timing import RequestTiming
from ..cognition import run_cognitive_cycle
from ..guards.guillotine import StreamingGuillotine, GuillotineViolation
from ..pipeline.sentence_stream import StreamingSpeech
from ..singing.intent import detect_singing_intent
from ..singing.pipeline import run_singing_pipeline
from ..emotions import (
    EmotionalState,
    compute_emotional_state,
    get_emotion_context_string,
    get_tts_hints,
    nudge_emotion,
)
from ..emotions.engine import circadian_affect_overlay, circadian_energy_label
from ..emotions.indextts_bridge import affect_to_emo_vector
from ..rag import inject_rag_facts
from ..telemetry import preference_logger
from ..thoughts import get_current_thought
from ..capability_awareness import build_capability_block
from ..self_history import should_inject_history, get_history_block
from ..senses.vision import describe_images
from ..mood_modifiers import compute_global_modifier_scale, get_active_modifier_labels, track_social_interaction
# Phase 1 endocrine simulation — body subsystem replaces label-based emotion
# decisions with causal hormone-level simulation. See docs/koroki_subsystem_atlas.md.
from ..body.endocrine import Event as BodyEvent, get_endocrine
from ..body.interoception import get_felt_state
# Phase 2B subsystems — presence tracker emits sustained_presence events as
# users spend continuous time online; memory stream writes important moments
# and feeds them back to the body on recall.
from ..social.presence import get_presence
from ..mind.memory import get_memory
# Phase 2C: energy drains during interaction, sleep state can transition,
# memory consolidates on sleep.
from ..body.energy import get_energy
from ..body.sleep import get_sleep
from ..meta.sleep_cycle import get_sleep_cycle
# Phase 3: relationships (cross-session state), residue (emotional aftermath),
# proactive scheduler (autonomous initiation).
from ..social.relationship import get_relationships
from ..social.residue import get_residue
from ..meta.scheduler import get_scheduler
from ..autonomy.scheduler import build_proactive_signal
from ..nervous_system import (
    get_state, build_state_block, on_social_event,
    set_spotlight, add_rumination, consume_surfaced_rumination,
)
from ..topic_interests import build_topic_fact
from ..tool_executor import execute_tool_calls
from shared.utils.config import get_settings

router = APIRouter()
logger = logging.getLogger("orchestrator.chat")


def _orchestrator_repair_layer_enabled(name: str, default: bool = True) -> bool:
    try:
        return bool(get_settings().get("repair_layers", {}).get(name, default))
    except Exception:
        return default


def _tts_repair_layer_enabled(name: str, default: bool = True) -> bool:
    try:
        return bool(
            get_settings()
            .get("models", {})
            .get("tts", {})
            .get("repair_layers", {})
            .get(name, default)
        )
    except Exception:
        return default

def _clip_tts_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text

    clipped = text[:max_chars].rstrip()
    last_stop = max(clipped.rfind("."), clipped.rfind("!"), clipped.rfind("?"))
    if last_stop >= int(max_chars * 0.6):
        clipped = clipped[: last_stop + 1]
    return clipped.strip()


_ACTION_PATTERN = re.compile(r"\*[^*\n]+\*|\([^)\n]+\)|\[[^\]\n]+\]")
_ACTION_CAPTURE_PATTERN = re.compile(r"\*([^*\n]+)\*|\(([^)\n]+)\)|\[([^\]\n]+)\]")

# Emphasis-aware TTS action detection.
# **bold** → always emphasis (keep text, strip markers)
# *verb phrase* / *..., ...* → stage direction (strip entirely)
# *single non-verb word* / *short non-verb phrase* → inline emphasis (keep text, strip markers)
_DOUBLE_STAR_PATTERN = re.compile(r"\*\*([^*\n]+)\*\*")
_SINGLE_STAR_PATTERN = re.compile(r"\*([^*\n]+)\*")

# Present-tense verbs and manner adverbs that almost always open a stage direction.
_ACTION_VERB_STARTERS = frozenset({
    "smiles", "smile", "tilts", "tilt", "leans", "lean", "steps", "step",
    "glances", "glance", "looks", "look", "turns", "turn", "raises", "raise",
    "nods", "nod", "sighs", "sigh", "laughs", "laugh", "giggles", "giggle",
    "whispers", "whisper", "reaches", "reach", "waves", "wave", "shrugs", "shrug",
    "blinks", "blink", "shifts", "shift", "narrows", "narrow", "softens", "soften",
    "bows", "bow", "moves", "move", "places", "place", "rests", "rest",
    "stands", "stand", "sits", "sit", "walks", "walk", "crosses", "cross",
    "extends", "extend", "gestures", "gesture", "frowns", "frown", "pauses", "pause",
    "breathes", "breathe", "chuckles", "chuckle", "hums", "hum", "taps", "tap",
    "grins", "grin", "smirks", "smirk", "stares", "stare", "gazes", "gaze",
    "watches", "watch", "approaches", "approach", "settles", "settle",
    "presses", "press", "traces", "trace", "catches", "catch", "holds", "hold",
    "quietly", "gently", "slowly", "softly", "carefully", "suddenly", "lightly",
})


def _is_action_span(inner: str) -> bool:
    """Return True if asterisk-enclosed text is a stage direction, False if inline emphasis."""
    s = inner.strip()
    if not s:
        return False
    # Comma → always a stage direction (e.g. *leans back, eyes glinting with amusement*)
    if any(ch in s for ch in (",", ".", "!", "?", ";", ":")):
        return True
    words = s.split()
    if not words:
        return False
    first = words[0].lower()
    # Single-word spans: match known verbs, including hyphenated forms like *half-smiles*.
    if len(words) == 1:
        if first in _ACTION_VERB_STARTERS:
            return True
        # "half-smiles", "half-turns", etc. — check each hyphen segment
        parts = first.split("-")
        return any(p in _ACTION_VERB_STARTERS for p in parts)
    # Multi-word spans are ALWAYS stage directions (owner rule 2026-07-03: nothing
    # inside *...* may be spoken). The old heuristic kept 2-word non-verb phrases as
    # "emphasis" — which made her SAY "small smile" out loud. Only single-word
    # emphasis (*truly*, *exactly*) is still spoken.
    return True


def _action_replacement_punctuation(action_text: str) -> str:
    """Map stripped action text to natural pause punctuation for smoother TTS pacing."""
    lowered = action_text.lower().strip()
    if any(token in lowered for token in ("pause", "paused", "silence", "silent", "hesitat", "breath", "sigh")):
        return ". "
    return ", "

_EMOTION_CUE_RULES: list[tuple[tuple[str, ...], str]] = [
    (("giggle", "laugh", "chuckle"), "with a soft laugh,"),
    (("wink", "tease", "playful"), "playfully,"),
    (("blush", "shy", "embarrassed"), "a little shyly,"),
    (("sigh",), "with a gentle sigh,"),
    (("whisper", "softly", "quiet"), "softly,"),
    (("angry", "annoyed", "frown"), "in a firmer tone,"),
    (("surprised", "shock", "startled"), "surprised,"),
]

_EMOTION_CUE_RULES_SIMPLE: list[tuple[tuple[str, ...], str]] = [
    (("giggle", "laugh", "chuckle", "wink", "tease", "playful"), "happy,"),
    (("blush", "shy", "embarrassed"), "shy,"),
    (("sigh", "whisper", "softly", "quiet"), "gentle,"),
    (("angry", "annoyed", "frown"), "serious,"),
    (("surprised", "shock", "startled"), "surprised,"),
]

_LEADING_STAGE_DIRECTION_PATTERN = re.compile(
    r"^\s*(?:"
    r"eyes?\b[^,.;!?\n]{0,80},\s*|"
    r"(?:soft|gentle)\s+gaze\b[^,.;!?\n]{0,80},\s*|"
    r"(?:smiling|smiles|blushing|blushes|leaning|leans|tilting|tilts|giggling|giggles)\b[^,.;!?\n]{0,80},\s*|"
    r"(?:gentle\s+tilt\s+of\s+head|head\s+tilting\s+upward)\b[^,.;!?\n]{0,80},\s*"
    r")",
    re.IGNORECASE,
)

_STOCK_TAIL_PHRASES = (
    "just for us-now?",
    "just for us-now.",
    "just for you-now?",
    "just for you-now.",
    "what do you think?",
)

_MENTION_ID_PATTERN = re.compile(r"<@!?(\d{5,25})>")

_THINK_BLOCK_PATTERN = re.compile(
    r"<\s*(?:think|thinking)\s*>[\s\S]*?<\s*/\s*(?:think|thinking)\s*>",
    re.IGNORECASE,
)
_THINK_UNCLOSED_PATTERN = re.compile(
    r"<\s*(?:think|thinking)\s*>[\s\S]*$",
    re.IGNORECASE,
)
_THINK_TAG_PATTERN = re.compile(r"<\s*/?\s*(?:think|thinking)\s*>", re.IGNORECASE)


_CLAUSE_PATTERN = re.compile(r"[^.!?;\n]+[.!?;]?")
_WORD_PATTERN = re.compile(r"\b\w+\b")
_CONJUNCTION_PATTERN = re.compile(
    r"\b(and|but|so|because|while|although|though|however|then)\b",
    re.IGNORECASE,
)
_PERIOD_BOUNDARY_PATTERN = re.compile(r"\.\s+(?=[A-Z])")
_EMOTION_TAG_PATTERN = re.compile(r"\[(?:emo|emotion)\s*:\s*([a-z_]+?)(\d{1,2})?\]", re.IGNORECASE)

# All emotion cues are non-verbal (empty strings) so emotion tags are passed as metadata
# to TTS without speaking artificial adverbs. The emotion metadata (e.g., [emo:cold])
# gets processed by TTS engines to adjust voice tone SILENTLY.
_EMOTION_TAG_CUE_MAP: dict[str, str] = {
    "neutral": "",
    "happy": "",
    "sad": "",
    "angry": "",
    "fear": "",
    "surprise": "",
    "awe": "",
    "shy": "",
    "embarrassed": "",
    "lonely": "",
    "jealous": "",
    "tsun": "",
    "awkwardness": "",
    "caring": "",
    "protective": "",
    "relieved": "",
    "worried": "",
    "anxiety": "",
    "annoyed": "",
    "frustrated": "",
    "disappointed": "",
    "sarcastic": "",
    "playful": "",
    "proud": "",
    "curious": "",
    "thoughtful": "",
    "pout": "",
    "cold": "",
    "sexual_desire": "",
    "whisper": "",
    "tired": "",
    "sleepy": "",
    "breathy": "",
    "monotone": "",
    "firm": "",
    "mumbling": "",
}

_AUTO_EMOTION_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    # caring — covers owner warmth, attachment expressions, and comfort
    (("miss", "glad", "dear", "love", "cherish", "warm", "thinking about you",
      "i was just thinking", "you're here", "come here", "stay", "glad you're",
      "glad you came", "i care", "here for you", "sit with me", "be with"), "caring"),
    # playful — teasing, light energy, banter
    (("tease", "smirk", "wink", "hehe", "~", "pfft", "oh please", "admit it",
      "you would", "you're so", "as if", "obviously", "right", "sure you do"), "playful"),
    # whisper — intimate, hushed, close
    (("hush", "closer", "lean in", "whisper", "quiet now", "just between"), "whisper"),
    # protective — guarding, shielding, concern
    (("wait", "careful", "protect", "safe", "don't", "stop that", "i won't let"), "protective"),
    # curious — inquiry, interest, engagement
    (("interesting", "tell me", "i wonder", "what do you", "curious", "go on",
      "i want to know", "how does", "what makes", "i didn't know"), "curious"),
    # annoyed — irritation, frustration, pushback
    (("angry", "furious", "enough", "stop", "annoyed", "irritating", "really",
      "seriously", "do you always", "of course you", "why would you"), "annoyed"),
    # sad — melancholy, regret, empathy for pain
    (("sorry", "regret", "pity", "sad", "hurt", "it's hard", "that's rough",
      "i understand", "must be", "i know how"), "sad"),
    # cold — detachment, composure, distance
    (("cold", "distant", "aloof", "detached", "indifferent", "not my problem",
      "i don't see why", "that's your", "do what you want"), "cold"),
    # thoughtful — reflection, measured response
    (("thinking", "thought about", "i've been", "processing", "consider",
      "actually", "in a way", "it's more like", "complicated"), "thoughtful"),
]


def _normalize_clause(text: str) -> str:
    compact = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    compact = re.sub(r"\s+", " ", compact).strip()
    return compact


# Phrases that are deeply baked into the model's training as cold-stranger defaults.
# These are crutches — not wrong in every context, but the model overuses them ~90% of the
# time for minimal inputs regardless of the actual situation. We detect them post-generation
# and trigger a single retry with the phrase explicitly forbidden so the model must vary.
_CRUTCH_PATTERNS = [
    re.compile(r"(?i)you[‘’]?re\s+here[.,!]?\s+that[‘’]?s\s+something"),
    re.compile(r"(?i)not\s+the\s+first\s+to\s+say\s+that"),
    re.compile(r"(?i)you[‘’]?re\s+not\s+the\s+(?:first|type|kind)\s+to"),
]
# Canonical string forms of all crutch phrases — passed as forbidden on retry so the model
# can’t fall back to any variant, not just the one that triggered detection.
_CRUTCH_CANONICAL_PHRASES = [
    "you’re here. that’s something",
    "not the first to say that",
    "you’re not the type to",
    "you’re not the first to",
    "you’re not the kind to",
]
_ACTION_STRIP_RE = re.compile(r"\*[^*\n]+\*")


def _detect_crutch_phrase(text: str) -> str | None:
    """Return the matched crutch sentence if the response is dominated by a known template.

    Only triggers when the non-action content is short (≤80 chars), meaning the crutch
    IS the response rather than incidental in a longer one. Longer responses that happen
    to contain the phrase are fine — the phrase isn't the whole point there.
    """
    stripped = _ACTION_STRIP_RE.sub("", text).strip()
    if len(stripped) > 80:
        return None
    # Normalize mojibake apostrophes: U+00E2 U+0080 U+0099 → U+2019 (RIGHT SINGLE QUOTATION MARK)
    # Ollama streams UTF-8 bytes that sometimes arrive double-encoded.
    normalized = stripped.replace("â", "’").replace("â", "“").replace("â", "”")
    for pat in _CRUTCH_PATTERNS:
        m = pat.search(normalized)
        if m:
            return m.group(0).strip()
    return None


def _trim_repetitive_tail(
    text: str,
    threshold: float = 0.80,
    min_clause_chars: int = 24,
) -> tuple[str, bool, float | None]:
    clauses = [m.group(0).strip() for m in _CLAUSE_PATTERN.finditer(text) if m.group(0).strip()]
    if len(clauses) < 2:
        return text, False, None

    kept: list[str] = []
    normalized_kept: list[str] = []
    trim_ratio: float | None = None

    for clause in clauses:
        normalized = _normalize_clause(clause)
        if len(normalized) >= min_clause_chars:
            for previous in normalized_kept:
                ratio = SequenceMatcher(None, normalized, previous).ratio()
                if ratio >= threshold:
                    trim_ratio = ratio
                    trimmed_text = " ".join(kept).strip()
                    if not trimmed_text:
                        return text, False, None
                    trimmed_text = re.sub(r"\s+([,.;!?])", r"\1", trimmed_text)
                    return trimmed_text, True, trim_ratio

        kept.append(clause)
        normalized_kept.append(normalized)

    return text, False, None


def _extract_mentioned_user_ids(message: str, explicit_ids: list[str] | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for user_id in explicit_ids or []:
        uid = str(user_id).strip()
        if uid and uid not in seen:
            seen.add(uid)
            out.append(uid)
    for match in _MENTION_ID_PATTERN.findall(message or ""):
        uid = str(match).strip()
        if uid and uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out


def _sanitize_persistent_core_facts(core_facts: list[str] | None, max_core_facts: int) -> list[str]:
    """Remove runtime-only prompt facts that should never be stored long-term."""
    if not core_facts:
        return []

    runtime_prefixes = (
        "## Current Emotional Context",
        "## Cognitive Context",
        "current_speaker_is_owner=",
        "owner_mentioned=",
        "mentioned_owner_name=",
        "mentioned_user_id=",
        "mentioned_user_fact_",
        "owner_known_fact=",
        "cognitive_",
        "days_since_last_contact=",
        "time_of_day=",
        "energy_state=",
        "conversation_depth=",
        "session_length_hint=",
        "koroki_boredom_signal=",
        "mood_momentum=",
        "message_is_minimal=",
        "last_opener_emotion=",
        "mood_thawing=",
        "mood_cooling=",
        "emotional_ambivalence=",
        "withdrawal_instinct=",
    )

    cleaned: list[str] = []
    for fact in core_facts:
        fact_text = str(fact or "").strip()
        if not fact_text:
            continue
        if fact_text.startswith(runtime_prefixes):
            continue
        cleaned.append(fact_text)

    deduped: list[str] = []
    seen: set[str] = set()
    for fact in cleaned:
        if fact not in seen:
            deduped.append(fact)
            seen.add(fact)

    return deduped[:max_core_facts]


async def _build_mention_bridge_facts(
    requester_user_id: str,
    mentioned_user_ids: list[str],
    max_facts: int,
) -> tuple[list[str], list[str]]:
    """Build lightweight cross-user memory hints for explicitly mentioned users."""
    owner_cfg = get_settings().get("models", {}).get("brain", {}).get("owner_profile", {})
    owner_name = str(owner_cfg.get("display_name", "Koro")).strip()

    def _relation_descriptor(score: int) -> str:
        if score >= 90:
            return "deeply cherished"
        if score >= 70:
            return "trusted and close"
        if score >= 50:
            return "warmly familiar"
        if score >= 30:
            return "known but still measured"
        return "mostly distant"

    facts: list[str] = []
    known_users: list[str] = []
    for mentioned_id in mentioned_user_ids:
        if mentioned_id == requester_user_id:
            continue
        payload = await user_memory_cache.get(mentioned_id)
        if not payload:
            continue

        known_users.append(mentioned_id)
        rel = payload.get("relationship_score")
        if isinstance(rel, int):
            if bool(payload.get("is_owner", False)):
                facts.append(f"owner_mentioned=true")
                facts.append(f"mentioned_owner_name={owner_name}")
            else:
                facts.append(
                    f"mentioned_user_id={mentioned_id}; familiarity={rel}/100; relation_descriptor={_relation_descriptor(rel)}"
                )

        for fact in (payload.get("core_facts") or [])[:1]:
            fact_text = str(fact).strip()
            if fact_text:
                if bool(payload.get("is_owner", False)):
                    facts.append(f"owner_known_fact={fact_text}")
                else:
                    facts.append(f"mentioned_user_fact_{mentioned_id}={fact_text}")
                break

        if len(facts) >= max_facts:
            break

    return facts[:max_facts], known_users


def _strip_thinking_artifacts(text: str) -> tuple[str, bool]:
    """Remove visible chain-of-thought tags/blocks before user-facing output."""
    removed = False
    updated = text
    # 1. Strip complete <think>…</think> blocks
    stripped_blocks = _THINK_BLOCK_PATTERN.sub("", updated)
    if stripped_blocks != updated:
        removed = True
        updated = stripped_blocks
    # 2. Strip unclosed blocks (token budget truncated generation mid-think —
    #    closing tag never arrived, so everything from <think> to end is reasoning)
    stripped_unclosed = _THINK_UNCLOSED_PATTERN.sub("", updated)
    if stripped_unclosed != updated:
        removed = True
        updated = stripped_unclosed
    # 3. Strip any leftover orphan tags
    stripped_tags = _THINK_TAG_PATTERN.sub("", updated)
    if stripped_tags != updated:
        removed = True
        updated = stripped_tags
    updated = re.sub(r"\s{2,}", " ", updated).strip()
    return updated, removed


def _action_to_tts_cue(action_text: str, style: str = "adverb") -> str | None:
    lowered = action_text.lower().strip()
    if not lowered:
        return None
    rules = _EMOTION_CUE_RULES_SIMPLE if style == "simple" else _EMOTION_CUE_RULES
    for keywords, cue in rules:
        if any(keyword in lowered for keyword in keywords):
            return cue
    return None


def _inject_tts_emotion_cues(text: str, max_cues: int = 2, style: str = "adverb") -> tuple[str, int]:
    base = _strip_tts_actions(text)
    if max_cues <= 0:
        return base, 0

    matched_cues: list[str] = []
    for match in _ACTION_CAPTURE_PATTERN.finditer(text):
        action = (match.group(1) or match.group(2) or match.group(3) or "").strip()
        # Single-star spans may be emphasis. Only treat them as action cues when classified.
        if match.group(1) is not None and not _is_action_span(action):
            continue
        cue = _action_to_tts_cue(action, style=style)
        if cue:
            matched_cues.append(cue)
            if len(matched_cues) >= max_cues:
                break

    if not matched_cues:
        return base, 0

    # Apply a single leading cue so spoken output stays natural and avoids
    # awkward trailing adverbs like "warmly, just for you".
    leading_cue = matched_cues[0]
    speech = f"{leading_cue} {base}".strip()
    return speech, len(matched_cues)


def _strip_tts_actions(text: str) -> str:
    """Remove stage-direction action markers before TTS, keeping inline emphasis.

    Distinguishes between:
    - **word** → markdown bold/emphasis → kept as plain text, markers stripped
    - *smiles* / *tilts head* / *leans back, eyes glinting* → stage direction → stripped
    - *unexpected* / *truly* / *my dear* → inline emphasis → kept as plain text
    - (action) and [action] → always stripped (not an emphasis syntax in Koroki's style)
    """
    if not _tts_repair_layer_enabled("action_stripping_enabled", True):
        return text
    # Process **bold** first so the single-star pass does not consume one star at a time.
    result = _DOUBLE_STAR_PATTERN.sub(lambda m: m.group(1), text)
    # *span* → action (replace with punctuation pause) or emphasis (keep text)
    result = _SINGLE_STAR_PATTERN.sub(
        lambda m: _action_replacement_punctuation(m.group(1)) if _is_action_span(m.group(1)) else m.group(1),
        result,
    )
    # (action) and [action] → replace with a short pause
    result = re.sub(r"\([^)\n]+\)|\[[^\]\n]+\]", ", ", result)
    result = re.sub(r"\s+([,.;!?])", r"\1", result)
    return re.sub(r"\s{2,}", " ", result).strip()


def _stabilize_tts_tail(text: str) -> str:
    """Repair truncated or dangling tails before synthesis to prevent glitchy artifacts."""
    s = str(text or "").strip()
    if not s:
        return s

    # Drop dangling markdown/action remnants at the end.
    s = re.sub(r"\*+$", "", s).strip()
    s = re.sub(r"[\(\[\{][^\)\]\}]*$", "", s).strip()
    if not s:
        return ""

    if s.endswith((".", "!", "?", "...", "…")):
        return s

    # If generation stopped mid-thought, backtrack to the latest sentence boundary.
    last_stop = max(s.rfind("."), s.rfind("!"), s.rfind("?"), s.rfind(";"), s.rfind(":"))
    if last_stop >= int(len(s) * 0.45):
        return s[: last_stop + 1].rstrip()

    # Fallback: force a clean sentence ending.
    s = re.sub(r"[\s,;:\-]+$", "", s).strip()
    if s and s[-1].isalnum():
        s += "."
    return s


def _sanitize_tts_speech_text(text: str) -> str:
    """Clean display-style narrative fragments from TTS-only speech text."""
    if not _tts_repair_layer_enabled("sanitize_speech_text_enabled", True):
        return text.strip()
    speech = _strip_tts_actions(text)

    # Remove up to two leading narrative action fragments that sound unnatural when spoken.
    for _ in range(2):
        updated = _LEADING_STAGE_DIRECTION_PATTERN.sub("", speech, count=1).strip()
        if updated == speech:
            break
        speech = updated

    # Drop repetitive stock closers from the tail to reduce template-like endings in speech.
    lowered = speech.lower().replace("—", "-")
    for phrase in _STOCK_TAIL_PHRASES:
        if lowered.endswith(phrase):
            cut = len(speech) - len(phrase)
            speech = speech[:cut].rstrip(" ,;:-")
            break

    speech = re.sub(r"\s{2,}", " ", speech).strip()
    return speech


def _shape_tts_pacing_text(
    text: str,
    long_clause_word_threshold: int = 18,
    max_pause_insertions: int = 1,
) -> str:
    """Add subtle punctuation pauses to long flat clauses for more natural TTS rhythm."""
    if not text or long_clause_word_threshold < 8 or max_pause_insertions <= 0:
        return text

    # Keep sentence delimiters by splitting with capture group.
    chunks = re.split(r"([.!?])", text)
    rebuilt: list[str] = []

    for idx in range(0, len(chunks), 2):
        sentence = (chunks[idx] or "").strip()
        punct = chunks[idx + 1] if idx + 1 < len(chunks) else ""
        if not sentence:
            if punct:
                rebuilt.append(punct)
            continue

        if "," in sentence or ";" in sentence or ":" in sentence:
            rebuilt.append(sentence + punct)
            continue

        words = _WORD_PATTERN.findall(sentence)
        if len(words) < long_clause_word_threshold:
            rebuilt.append(sentence + punct)
            continue

        insertions = 0
        updated_sentence = sentence
        for match in _CONJUNCTION_PATTERN.finditer(sentence):
            if insertions >= max_pause_insertions:
                break
            conj_index = match.start()
            # Favor a near-midpoint pause to avoid odd openings.
            if conj_index < len(sentence) * 0.28 or conj_index > len(sentence) * 0.82:
                continue
            updated_sentence = updated_sentence[:conj_index].rstrip() + ", " + updated_sentence[conj_index:].lstrip()
            insertions += 1
            break

        rebuilt.append(updated_sentence + punct)

    shaped = " ".join(part.strip() for part in rebuilt if part and part.strip())
    shaped = re.sub(r"\s+([,.;!?])", r"\1", shaped)
    shaped = re.sub(r"\s{2,}", " ", shaped).strip()
    return shaped


def _soften_tts_period_pauses(text: str, max_replacements: int = 1) -> str:
    """Reduce abrupt full-stop pause count by converting selected sentence boundaries to semicolons."""
    if not text or max_replacements <= 0:
        return text
    softened = _PERIOD_BOUNDARY_PATTERN.sub("; ", text, count=max_replacements)
    softened = re.sub(r"\s{2,}", " ", softened).strip()
    return softened


def _emotion_tag_to_cue(tag_name: str) -> str | None:
    normalized = str(tag_name or "").strip().lower()
    if not normalized:
        return None
    return _EMOTION_TAG_CUE_MAP.get(normalized)


def _apply_explicit_emotion_tags(
    text: str,
    max_tags: int = 4,
    render_cues: bool = False,
) -> tuple[str, int, list[str]]:
    """Parse explicit tags like [emo:happy2], optionally rendering cue phrases."""
    if not text or max_tags <= 0:
        return text, 0, []

    seen_tags: list[str] = []
    applied = 0

    def _replace(match: re.Match) -> str:
        nonlocal applied
        tag_name = str(match.group(1) or "").strip().lower()
        tag_level = str(match.group(2) or "").strip()
        full_tag = f"{tag_name}{tag_level}" if tag_level else tag_name
        if applied >= max_tags:
            return ""

        cue = _emotion_tag_to_cue(tag_name)
        seen_tags.append(full_tag)
        if cue is None:
            return ""
        if not cue:
            applied += 1
            return ""

        if not render_cues:
            applied += 1
            return ""

        applied += 1
        return f" {cue} "

    transformed = _EMOTION_TAG_PATTERN.sub(_replace, text)
    transformed = re.sub(r"\s+([,.;!?])", r"\1", transformed)
    transformed = re.sub(r"\s{2,}", " ", transformed).strip()
    return transformed, applied, seen_tags


def _normalize_tts_surface_text(text: str) -> str:
    """Normalize punctuation artifacts introduced by cue/action transforms."""
    out = str(text or "")
    out = re.sub(r",\s*,+", ", ", out)
    out = re.sub(r"\.{2,}", "...", out)
    out = re.sub(r";\s*;+", "; ", out)
    out = re.sub(r"([!?])\1+", r"\1", out)
    out = re.sub(r"\.{3}\s*[;:,]", "... ", out)
    out = re.sub(r"([!?])\s*,\s*", r"\1 ", out)
    out = re.sub(r"\s+([,.;!?])", r"\1", out)
    out = re.sub(r"([,.;!?])(?=[A-Za-z])", r"\1 ", out)
    out = re.sub(r"^[,.;:\-]+\s*", "", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


def _split_tts_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", str(text or "").strip())
    return [p.strip() for p in parts if p and p.strip()]


def _infer_sentence_emotion_tags(
    text: str,
    is_owner: bool,
    relationship_score: int,
    emotion_hints=None,
    max_sentences: int = 4,
) -> list[str]:
    """Infer emotion tags for each sentence based on content and context.
    
    Smart heuristics:
    - Action/emotion keywords override defaults
    - Questions elicit playful tone for owner, neutral for others
    - First sentence sets baseline tone but never forced to 'cold'
    - Avoid defaulting to 'cold' tag (prevents 'coolly' spam)
    - Positive sentiment gets caring/happy, negative gets worried/sad
    """
    sentences = _split_tts_sentences(text)
    out: list[str] = []
    
    primary_hint = getattr(emotion_hints, "primary_tag", "neutral")
    warmth_hint = int(getattr(emotion_hints, "warmth", 50))
    arousal_hint = int(getattr(emotion_hints, "arousal", 40))

    for idx, sentence in enumerate(sentences[:max_sentences]):
        lowered = sentence.lower()
        tag = primary_hint if idx == 0 else "neutral"

        # === Stage 1: Explicit emotional content (highest priority) ===
        if any(k in lowered for k in ("whisper", "quiet", "hush", "closer", "lean", "intimate")):
            tag = "whisper"
        elif any(k in lowered for k in ("angry", "furious", "enough", "stop", "annoyed", "irritat")):
            tag = "annoyed"
        elif any(k in lowered for k in ("sorry", "regret", "fault", "disappoint")):
            tag = "sad"
        elif any(k in lowered for k in ("sad", "hurt", "pain", "ache", "mourn")):
            tag = "sad"
        elif any(k in lowered for k in ("care", "protect", "safe", "danger", "careful", "guard", "shield")):
            tag = "protective"
        elif any(k in lowered for k in ("tease", "wink", "smirk", "grin", "laugh", "~")):
            tag = "playful"
        elif any(k in lowered for k in ("miss", "dear", "love", "cherish", "affection", "adore")):
            tag = "caring"
        elif any(k in lowered for k in ("proud", "dignity", "grace", "nobility", "prestige")):
            tag = "proud"
        elif any(k in lowered for k in ("tire", "weary", "exhaust", "spent")):
            tag = "tired"
        elif any(k in lowered for k in ("fear", "afraid", "dread", "terror", "anxious")):
            tag = "anxiety"
        # === Stage 2: Sentence syntax (questions, imperatives) ===
        elif "?" in sentence:
            # Questions are inquisitive/playful for owner, genuinely curious for others
            if is_owner and warmth_hint >= 60:
                tag = "playful"
            elif arousal_hint <= 35:
                tag = "thoughtful"
            else:
                tag = "curious"
        elif any(s in lowered for s in ("must", "should", "have to", "need to", "ought")):
            # Directive sentences get firm/protective tone
            tag = "firm" if is_owner or warmth_hint < 45 else "protective"
        # === Stage 3: Tone markers via capitalization/punctuation ===
        elif "!!!" in sentence:
            tag = "playful"
        elif "..." in sentence:
            tag = "thoughtful"
        # === Stage 4: Default for first sentence only (no forced 'cold') ===
        elif idx == 0 and is_owner:
            # Owner always gets pleasant opening tone
            tag = "caring" if warmth_hint >= 58 else primary_hint
        # Otherwise, neutral is fine—don't force 'cold' on low-relationship users

        out.append(tag)
    return out


def _apply_sentence_emotion_cues(
    text: str,
    sentence_tags: list[str],
    max_verbal_cues: int = 2,
) -> str:
    if not text:
        return text
    sentences = _split_tts_sentences(text)
    if not sentences:
        return text

    verbal_applied = 0
    rebuilt: list[str] = []
    for idx, sentence in enumerate(sentences):
        tag = sentence_tags[idx] if idx < len(sentence_tags) else "neutral"
        cue = _emotion_tag_to_cue(tag) or ""
        if cue and verbal_applied < max_verbal_cues:
            rebuilt.append(f"{cue} {sentence}".strip())
            verbal_applied += 1
        else:
            rebuilt.append(sentence)

    out = " ".join(rebuilt)
    return _normalize_tts_surface_text(out)


def _infer_auto_emotion_tags(
    text: str,
    is_owner: bool,
    relationship_score: int,
    emotion_hints=None,
    max_tags: int = 2,
) -> list[str]:
    """Infer lightweight emotion tags from raw assistant text for TTS-only enrichment."""
    if not text or max_tags <= 0:
        return []

    lowered = text.lower()
    tags: list[str] = []

    primary_hint = getattr(emotion_hints, "primary_tag", None)
    secondary_hints = list(getattr(emotion_hints, "secondary_tags", []) or [])

    if primary_hint:
        tags.append(primary_hint)
    elif is_owner:
        tags.append("caring")
    elif relationship_score < 40:
        tags.append("cold")

    for tag in secondary_hints:
        if tag not in tags:
            tags.append(tag)
        if len(tags) >= max_tags:
            break

    for keywords, tag in _AUTO_EMOTION_KEYWORDS:
        if any(k in lowered for k in keywords):
            if tag == "curious":
                tag = "playful" if is_owner else "thoughtful"
            if tag and tag not in tags:
                tags.append(tag)
            if len(tags) >= max_tags:
                break

    if "?" in text and "curious" not in tags and len(tags) < max_tags:
        tags.append("playful" if is_owner else "thoughtful")

    # Drop neutral if something stronger is present.
    if len(tags) > 1 and "neutral" in tags:
        tags = [t for t in tags if t != "neutral"]

    return tags[:max_tags]


def _inject_auto_emotion_tags(text: str, tags: list[str]) -> str:
    if not text or not tags:
        return text
    prefix = " ".join(f"[emo:{tag}1]" for tag in tags)
    return f"{prefix} {text}".strip()



def _detect_repeated_phrases(recent_turns: list[dict], min_len: int = 18) -> list[str]:
    """Return sentences that appear verbatim in 2+ of the last 3 assistant turns.

    Used to build a targeted forbidden-phrase list so the brain doesn't anchor on
    a single phrasing and repeat it across multiple replies to different messages.
    """
    assistant_texts = [
        t["content"] for t in (recent_turns or [])
        if t.get("role") == "assistant" and t.get("content", "").strip()
    ][-3:]
    if len(assistant_texts) < 2:
        return []
    # Strip stage directions, split on sentence-ending punctuation.
    _stage = re.compile(r"\*[^*\n]+\*")
    repeated: list[str] = []
    seen: dict[str, int] = {}
    for text in assistant_texts:
        clean = _stage.sub("", text)
        for part in re.split(r"[.!?\n]", clean):
            phrase = part.strip()
            if len(phrase) >= min_len:
                key = phrase.lower()
                seen[key] = seen.get(key, 0) + 1
    for phrase_lower, count in seen.items():
        if count >= 2:
            repeated.append(phrase_lower)
    return repeated[:6]


def _should_enable_thinking(message: str, is_owner: bool, relationship_score: int) -> bool:
    """Disable thinking mode when LoRA is active.

    The koroki_4b LoRA was trained without <think> tokens — when thinking is
    ON, the model generates real reasoning blocks that leak through rather than
    immediately closing </think>. The LoRA provides character guidance directly,
    so thinking overhead is unnecessary.
    """
    return False


_SYCOPHANT_PATTERNS = re.compile(
    r"(?i)\b(of course[!,]|absolutely[!,]|certainly[!,]|great question|sure thing"
    r"|happy to|glad to|i'd love to|no problem[!,]|right away)",
)
_WARM_OWNER_MARKERS = frozenset({"koro", "you", "yours", "we", "us", "our"})


def _reflect_on_response(
    text: str,
    emotion: str,
    relationship_score: int,
    is_owner: bool,
) -> list[str]:
    """Rule-based self-consistency check. Returns list of issues found (empty = clean).

    Checks:
    1. Sycophantic openers that slipped past the guillotine
    2. Response too long for clipped emotional states (annoyed/cold)
    3. Warmth markers missing for owner/high-relationship responses
    4. Tone-relationship mismatch (very intimate language for strangers)
    """
    issues: list[str] = []
    if not text:
        return issues

    # 1. Sycophant check
    if _SYCOPHANT_PATTERNS.search(text[:80]):
        issues.append("sycophantic_opener")

    # 2. Length vs emotion
    word_count = len(text.split())
    if emotion in {"annoyed", "frustrated", "ragebaited", "cold"} and word_count > 60:
        issues.append(f"too_long_for_{emotion}({word_count}w)")

    # 3. Owner warmth check — owner responses should feel personal, not generic
    if is_owner and word_count > 10:
        lowered = text.lower()
        if not any(m in lowered for m in _WARM_OWNER_MARKERS):
            issues.append("missing_owner_warmth_markers")

    # 4. Intimacy-relationship mismatch
    if relationship_score < 25 and not is_owner:
        lowered = text.lower()
        intimate_terms = {"darling", "sweetheart", "my dear", "love", "precious", "baby"}
        if any(t in lowered for t in intimate_terms):
            issues.append("intimate_language_for_stranger")

    return issues


def _classify_text_emotion(text: str, is_owner: bool) -> str | None:
    """Quick keyword scan of Brain's response text to infer what emotion was expressed.

    Used for divergence detection — comparing intended emotion (from emotion engine)
    against what Brain actually wrote. Not a replacement for the full inference pipeline;
    just fast enough to catch obvious mismatches and log them.
    """
    # Normalize Unicode punctuation so keyword matches work regardless of quote style
    lowered = (
        text.lower()
        .replace("’", "'")   # right single quotation mark → apostrophe
        .replace("‘", "'")   # left single quotation mark
        .replace("“", '"')   # left double quote
        .replace("”", '"')   # right double quote
    )
    for keywords, tag in _AUTO_EMOTION_KEYWORDS:
        if any(k in lowered for k in keywords):
            return tag
    if is_owner:
        return "caring"
    return None


# ---------------------------------------------------------------------------
# #5 — TTS text preprocessing: strip markdown, URLs, emoji, abbreviations
# ---------------------------------------------------------------------------

_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BACKTICK_RE = re.compile(r"`{1,3}([^`\n]*)`{1,3}")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001FA00-\U0001FAFF]+",
    flags=re.UNICODE,
)
_TTS_ABBREVS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\be\.g\.", re.IGNORECASE), "for example"),
    (re.compile(r"\bi\.e\.", re.IGNORECASE), "that is"),
    (re.compile(r"\betc\.", re.IGNORECASE), "et cetera"),
    (re.compile(r"\bvs\.", re.IGNORECASE), "versus"),
    (re.compile(r"\bbtw\b", re.IGNORECASE), "by the way"),
    (re.compile(r"\bidk\b", re.IGNORECASE), "I don't know"),
    (re.compile(r"\btbh\b", re.IGNORECASE), "to be honest"),
    (re.compile(r"\bngl\b", re.IGNORECASE), "not gonna lie"),
    (re.compile(r"\bomg\b", re.IGNORECASE), "oh my god"),
    (re.compile(r"\blol\b", re.IGNORECASE), ""),
    (re.compile(r"\blmao\b", re.IGNORECASE), ""),
    (re.compile(r"\bDr\.", re.IGNORECASE), "Doctor"),
    (re.compile(r"\bMr\.", re.IGNORECASE), "Mister"),
    (re.compile(r"\bMrs\.", re.IGNORECASE), "Missus"),
]


def _preprocess_tts_text(text: str) -> str:
    """Strip markdown artifacts, URLs, emoji, and expand TTS-hostile abbreviations."""
    t = str(text or "")
    t = _MARKDOWN_HEADING_RE.sub("", t)
    t = _BACKTICK_RE.sub(r"\1", t)
    t = _URL_RE.sub("", t)
    t = _EMOJI_RE.sub("", t)
    for pat, replacement in _TTS_ABBREVS:
        t = pat.sub(replacement, t)
    return re.sub(r"\s{2,}", " ", t).strip()


# ---------------------------------------------------------------------------
# #3 — Breath / filler injection: emotion-driven pause markers
# ---------------------------------------------------------------------------

def _inject_breath_filler(text: str, emotion_label: str, fatigue: int, intensity: int) -> str:
    """Inject natural pause/filler markers based on Koroki's emotional state.

    tired/fatigued  → ellipsis inter-sentence pause (halting, heavy delivery)
    playful         → em-dash at one clause break (casual, energetic)
    angry states    → no modification; emo_vector already drives delivery
    """
    if not text:
        return text
    if emotion_label in ("annoyed", "frustrated", "ragebaited"):
        return text
    if emotion_label == "tired" or fatigue > 65:
        # First mid-text ". " boundary → "... " for a natural breath-pause
        text = re.sub(r"\.\s+(?=[A-Z])", "... ", text, count=1)
    elif emotion_label == "playful" and intensity > 55:
        # First ", and/but/so " → " — and/but/so " for casual dash-flow energy
        text = re.sub(r",\s+(and|but|so)\s+", r" — \1 ", text, count=1)
    return text


# ---------------------------------------------------------------------------
# #3b — Disfluency injection: uh/hmm/well before complex utterances
# ---------------------------------------------------------------------------

def _inject_disfluency(text: str, emotion_label: str, fatigue: int) -> str:
    """Prefix sufficiently complex first sentences with a natural filler word.

    Deterministic — same text always gets the same filler (based on content hash),
    so retries and replays sound consistent. Skipped for clipped emotional states.

    Filler probability: ~20% of qualifying sentences (>= 12 words in first clause).
    """
    import hashlib
    if not text or emotion_label in ("annoyed", "frustrated", "ragebaited"):
        return text

    # Measure complexity of the first sentence only.
    first_end = max(text.find(". "), text.find("! "), text.find("? "))
    first_clause = text[:first_end].strip() if first_end > 0 else text.strip()
    word_count = len(first_clause.split())
    if word_count < 12:
        return text

    # Deterministic ~20% gate using content hash.
    gate_byte = hashlib.sha1(first_clause.encode("utf-8", errors="ignore")).digest()[0]
    if gate_byte > 51:  # 51/255 ≈ 20% pass rate
        return text

    if emotion_label == "tired" or fatigue > 65:
        filler = "Hmm... "
    elif emotion_label in ("caring", "protective"):
        filler = "Well, "
    else:
        filler = "Uh, "

    # Lower-case first char of original text when appending after filler.
    return filler + text[0].lower() + text[1:]


# ---------------------------------------------------------------------------
# #2 — Response rhythm: emotion-derived pacing + token budget
# ---------------------------------------------------------------------------

def _tts_rhythm_params(emotion_label: str, fatigue: int) -> tuple[int, int]:
    """Return (clause_word_threshold, max_pause_insertions) for _shape_tts_pacing_text.

    Tired → short clauses, more pauses.  Angry → long threshold, no pauses (clipped).
    Playful → relaxed threshold.  Caring → steady mid pace.
    """
    if emotion_label == "tired" or fatigue > 65:
        return 12, 2
    if emotion_label in ("annoyed", "frustrated", "ragebaited"):
        return 30, 0
    if emotion_label == "playful":
        return 22, 1
    if emotion_label == "caring":
        return 15, 1
    return 18, 1


def _emotion_token_budget(base: int, emotion_label: str, fatigue: int, playfulness: int) -> int:
    """Scale brain max_new_tokens by emotional state (capped at ±25% of base).

    Koroki speaks less when angry or tired, more when playful or caring.
    """
    if emotion_label == "ragebaited":
        scale = 0.70
    elif emotion_label in ("annoyed", "frustrated"):
        scale = 0.80
    elif emotion_label == "tired" or fatigue > 65:
        scale = 0.75
    elif emotion_label == "playful" and playfulness > 60:
        scale = 1.15
    elif emotion_label == "caring":
        scale = 1.10
    else:
        scale = 1.0
    return max(24, int(base * scale))


@router.post("/chat")
async def chat(req: ChatRequest) -> dict:
    timing = RequestTiming(request_id=req.request_id)
    settings = get_settings()
    pre_generation_enabled = settings.get("features", {}).get("pre_generation", {}).get("enabled", False)
    memory_cfg = settings.get("memory", {})
    tts_cfg = settings.get("models", {}).get("tts", {})
    brain_cfg = settings.get("models", {}).get("brain", {})
    cognition_cfg = settings.get("cognition", {})
    max_recent_turns = int(memory_cfg.get("max_recent_turns", 8))
    max_core_facts = int(memory_cfg.get("max_core_facts", 20))
    mention_bridge_enabled = bool(memory_cfg.get("mention_bridge_enabled", True))
    mention_fact_limit = max(0, int(memory_cfg.get("mention_fact_limit", 2)))
    relationship_step_messages = max(1, int(memory_cfg.get("relationship_score_step_messages", 6)))
    max_tts_chars = int(tts_cfg.get("max_input_chars", 200))
    tts_emotion_cues_enabled = bool(tts_cfg.get("emotion_cues_enabled", True))
    max_tts_emotion_cues = int(tts_cfg.get("max_emotion_cues", 2))
    tts_emotion_cue_style = str(tts_cfg.get("emotion_cue_style", "adverb")).strip().lower()
    tts_explicit_emotion_tags_enabled = bool(tts_cfg.get("explicit_emotion_tags_enabled", True))
    tts_max_explicit_emotion_tags = max(0, int(tts_cfg.get("max_explicit_emotion_tags", 4)))
    tts_auto_emotion_tags_enabled = bool(tts_cfg.get("auto_emotion_tags_enabled", True))
    tts_max_auto_emotion_tags = max(0, int(tts_cfg.get("max_auto_emotion_tags", 2)))
    tts_pacing_shaping_enabled = bool(tts_cfg.get("pacing_shaping_enabled", True))
    tts_period_pause_softening_enabled = bool(tts_cfg.get("period_pause_softening_enabled", True))
    tts_period_pause_softening_max = int(tts_cfg.get("period_pause_softening_max_replacements", 1))
    repetition_tail_trim_enabled = _orchestrator_repair_layer_enabled("repetition_tail_trim_enabled", True)
    tts_action_stripping_enabled = _tts_repair_layer_enabled("action_stripping_enabled", True)
    tts_sanitize_speech_enabled = _tts_repair_layer_enabled("sanitize_speech_text_enabled", True)
    tts_sentence_emotion_cues_enabled = _tts_repair_layer_enabled("sentence_emotion_cues_enabled", True)
    discord_max_new_tokens = int(brain_cfg.get("discord_max_new_tokens", 128))
    web_max_new_tokens = int(brain_cfg.get("web_max_new_tokens", 64))
    cognition_enabled = bool(cognition_cfg.get("enabled", True))
    cognition_runtime_context_enabled = bool(cognition_cfg.get("runtime_context_enabled", True))
    cognition_max_runtime_facts = max(0, int(cognition_cfg.get("max_runtime_facts", 8)))
    is_web_platform = str(req.user_context.platform.value if hasattr(req.user_context.platform, "value") else req.user_context.platform) == "web"

    timing.mark("validate")

    # === Sight — her eyes look at attached images before anything reads the message ===
    # The vision service (moondream2, :9005) turns the image into a text percept.
    # Fail-soft: if her eyes are offline she is told an image exists that she
    # cannot see (honest AI-awareness), and the pipeline continues normally.
    _vision_desc: str | None = None
    _vision_blind = False
    if req.images_b64:
        _vision_desc = await describe_images(req.images_b64, request_id=req.request_id)
        if _vision_desc:
            logger.info(
                "[%s] Vision percept (%d chars): %s",
                req.request_id, len(_vision_desc), _vision_desc[:120],
            )
        else:
            _vision_blind = True
            logger.warning(
                "[%s] %d image(s) attached but vision unavailable — she can't see them",
                req.request_id, len(req.images_b64),
            )
        timing.mark("vision")
    # Message text as memory should keep it: what was said plus what she saw.
    _message_with_sight = (
        f"{req.message} [sent an image: {_vision_desc[:220]}]" if _vision_desc else req.message
    )

    cached_user_payload = await user_memory_cache.get(req.user_context.user_id)
    cached_user_payload = decay_memory_state(dict(cached_user_payload))
    merged_context = req.user_context.model_dump(mode="json")
    for key in ("relationship_score", "last_summary", "core_facts", "recent_turns"):
        if key in cached_user_payload and cached_user_payload[key] not in (None, [], ""):
            merged_context[key] = cached_user_payload[key]
    # is_owner is authoritative from the request (Discord bot validates against OWNER_DISCORD_ID).
    # Never override it from cache — cached state could be stale or wrong.
    # Owners always receive max score to ensure the correct voice profile and adapter are selected.
    if merged_context.get("is_owner", False):
        merged_context["relationship_score"] = 100
        merged_context["mode"] = "owner"
    else:
        # Prevent identity spoofing: mention content must never promote non-owner chats to owner behavior.
        existing_facts = list(merged_context.get("core_facts") or [])
        if "current_speaker_is_owner=false" not in existing_facts:
            existing_facts.append("current_speaker_is_owner=false")
            merged_context["core_facts"] = existing_facts[:max_core_facts]

    known_users = list(cached_user_payload.get("known_users") or [])

    # === Absence awareness — inject how long the user has been away ===
    _last_contact_ts = int(cached_user_payload.get("last_contact_at_ts", 0))
    _absence_days = (time.time() - _last_contact_ts) / 86400.0 if _last_contact_ts else 0.0
    _absence_fact: str | None = None
    if not req.proactive and _absence_days >= 1.0:
        _absence_days_rounded = round(_absence_days, 1)
        if _absence_days >= 7.0:
            _absence_fact = f"days_since_last_contact={_absence_days_rounded}; user_has_been_away_a_while"
        elif _absence_days >= 3.0:
            _absence_fact = f"days_since_last_contact={_absence_days_rounded}; it_has_been_several_days"
        else:
            _absence_fact = f"days_since_last_contact={_absence_days_rounded}"

    # === Time-of-day awareness (UTC+7 — Koroki's local time) ===
    _utc7 = timezone(timedelta(hours=7))
    _utc7_now = datetime.now(_utc7)
    _hour = _utc7_now.hour
    if 5 <= _hour < 12:
        _time_of_day = "morning"
    elif 12 <= _hour < 17:
        _time_of_day = "afternoon"
    elif 17 <= _hour < 22:
        _time_of_day = "evening"
    else:
        _time_of_day = "late_night"
    _day_rhythm = "weekend" if _utc7_now.weekday() >= 5 else "weekday"
    _time_fact = f"time_of_day={_time_of_day};day_rhythm={_day_rhythm}"
    _energy_fact = f"energy_state={circadian_energy_label(_utc7_now)}"

    # Global modifier scale — Koroki's own condition (sick, tired, well_rested, etc.)
    # combined with the time-of-day reactivity curve. Passed into nudge_emotion so the
    # same trigger produces different emotional movement depending on her current state.
    _modifier_scale = compute_global_modifier_scale(_utc7_now)
    _active_modifiers = get_active_modifier_labels()
    # Track interaction AFTER reading modifier scale so loneliness boost applies to this request
    # and fatigue from this interaction applies to future ones (not current).
    if not req.proactive:
        track_social_interaction(req.user_context.user_id)
        on_social_event("conversation")
        set_spotlight(req.user_context.user_id)

    # Conversation depth — how far into this session we are.
    # Lets the model know whether this is a quick opener or a deep-running exchange.
    _turn_count = len(merged_context.get("recent_turns") or [])
    if _turn_count <= 1:
        _conv_depth = "opening"
    elif _turn_count <= 5:
        _conv_depth = "engaged"
    elif _turn_count <= 10:
        _conv_depth = "deep"
    else:
        _conv_depth = "extended"
    _conv_depth_fact = f"conversation_depth={_conv_depth}"

    # Session energy drain — extended conversations naturally tire Koroki out.
    # The circadian/fatigue token budget already adjusts output length; this tells
    # the model explicitly that the session is long and social energy is dipping.
    _session_drain_fact: str | None = None
    if _turn_count >= 12:
        if circadian_energy_label() in ("low", "exhausted"):
            _session_drain_fact = "session_length_hint=very_long;social_energy_low;natural_close_instinct=true"
        else:
            _session_drain_fact = "session_length_hint=very_long;social_energy_running_low"
    elif _turn_count >= 8:
        _session_drain_fact = "session_length_hint=extended;energy_slightly_drained"

    persistent_core_facts = _sanitize_persistent_core_facts(
        merged_context.get("core_facts") or [],
        max_core_facts=max_core_facts,
    )
    merged_context["core_facts"] = list(persistent_core_facts)

    # === Load and compute emotional state (persistent per user) ===
    emotional_state_data = cached_user_payload.get("emotional_state", {})
    emotional_state = EmotionalState.from_dict(req.user_context.user_id, emotional_state_data)
    
    # Compute current mood with decay and relationship context
    emotional_state = compute_emotional_state(
        emotional_state,
        merged_context.get("relationship_score", req.user_context.relationship_score),
        merged_context.get("is_owner", False),
    )
    
    # Last-session emotion carry-forward — if Koroki was cold or irritated in the previous
    # session, she carries a trace of social guilt into this one (softens the start).
    # If she was warm, she starts with a small attachment lift. Only fires on the opener.
    if _turn_count == 0 and not req.proactive:
        _last_session_emo = str(cached_user_payload.get("last_session_emotion") or "").strip()
        if _last_session_emo in ("annoyed", "frustrated", "ragebaited", "cold"):
            emotional_state.affect_vector["irritation"] = max(
                0, emotional_state.affect_vector.get("irritation", 20) - 4
            )
            emotional_state.affect_vector["attachment"] = min(
                100, emotional_state.affect_vector.get("attachment", 50) + 3
            )
        elif _last_session_emo in ("caring", "playful", "protective"):
            emotional_state.affect_vector["attachment"] = min(
                100, emotional_state.affect_vector.get("attachment", 50) + 4
            )

    # Reunion energy burst — first message after a real absence triggers a transient
    # affect lift: curiosity (wondering what they've been up to) + attachment (genuine
    # "oh, you're back").  Only fires once because last_contact_at_ts is updated at
    # the end of this request, so subsequent messages in the same session won't qualify.
    if not req.proactive and _absence_days >= 2.0:
        _reunion_curiosity = min(10, max(3, int(_absence_days / 2)))
        emotional_state.affect_vector["curiosity"] = min(
            100, emotional_state.affect_vector.get("curiosity", 45) + _reunion_curiosity
        )
        emotional_state.affect_vector["attachment"] = min(
            100, emotional_state.affect_vector.get("attachment", 50) + 4
        )
        if _absence_days >= 5.0:
            emotional_state.affect_vector["fatigue"] = max(
                0, emotional_state.affect_vector.get("fatigue", 20) - 5
            )

    # Nudge emotion based on current message tone (skipped for proactive reach-outs)
    _rel_axes = dict(cached_user_payload.get("relationship_axes") or {})
    _trust_level = int(_rel_axes.get("trust", 20))
    _emotion_pre_nudge = emotional_state.current_emotion
    if not req.proactive:
        emotional_state = nudge_emotion(
            emotional_state,
            req.message,
            merged_context.get("relationship_score", req.user_context.relationship_score),
            trust_level=_trust_level,
            modifier_scale=_modifier_scale,
        )

    # Mood thaw — if Koroki just shifted out of a negative state into neutral/positive,
    # flag it so the model expresses the transition softly rather than snapping cheerful.
    # Real people who were annoyed don't instantly brighten; there's a brief exhale first.
    _NEGATIVE_EMOTIONS = {"cold", "annoyed", "frustrated", "ragebaited", "overstimulated"}
    _POSITIVE_EMOTIONS = {"caring", "playful", "protective", "proud"}
    _thaw_fact: str | None = None
    if (
        _emotion_pre_nudge in _NEGATIVE_EMOTIONS
        and emotional_state.current_emotion not in _NEGATIVE_EMOTIONS
    ):
        _thaw_fact = "mood_thawing=true;just_shifted_from_negative;ease_in_gently"
    elif (
        _emotion_pre_nudge in _POSITIVE_EMOTIONS
        and emotional_state.current_emotion in _NEGATIVE_EMOTIONS
    ):
        _thaw_fact = "mood_cooling=true;just_shifted_from_warm;do_not_mask_the_change"

    # Emotional ambivalence — when two opposing affect dimensions are both elevated,
    # Koroki is genuinely torn. Surface it so the model can express complexity rather
    # than defaulting to a clean single emotion. Humans often feel two things at once.
    _av = emotional_state.affect_vector
    _ambiv_fact: str | None = None
    if _av.get("playfulness", 35) >= 60 and _av.get("irritation", 20) >= 45:
        _ambiv_fact = "emotional_ambivalence=playful_but_edgy"
    elif _av.get("attachment", 50) >= 65 and _av.get("stress", 20) >= 45:
        _ambiv_fact = "emotional_ambivalence=caring_but_guarded"
    elif _av.get("curiosity", 45) >= 65 and _av.get("fatigue", 20) >= 55:
        _ambiv_fact = "emotional_ambivalence=interested_but_tired"

    # Frustration withdrawal instinct — if 3+ consecutive negative interactions hit
    # and trust is low, Koroki starts pulling back: shorter, less forthcoming.
    # Real people disengage when repeatedly mistreated; she should too.
    _true_consec_neg = 0
    for _ri in reversed(emotional_state.recent_interactions):
        if _ri.tone == "negative":
            _true_consec_neg += 1
        else:
            break
    _withdrawal_fact: str | None = None
    if _true_consec_neg >= 3 and _trust_level < 45:
        _withdrawal_fact = "withdrawal_instinct=true;repeated_friction_and_low_trust;be_brief_and_guarded"
    elif _true_consec_neg >= 4:
        _withdrawal_fact = "withdrawal_instinct=true;sustained_friction;disengage_gently"

    # Mood momentum — three consecutive same-tone interactions lock in an emotional groove.
    # Real people don't snap instantly from playful to annoyed on a single message; this
    # tells the model the current vibe has momentum and should carry through.
    _momentum_tones = [ri.tone for ri in emotional_state.recent_interactions[-3:]]
    _mood_momentum_fact: str | None = None
    if len(_momentum_tones) >= 3 and len(set(_momentum_tones)) == 1:
        _mood_momentum_fact = f"mood_momentum={_momentum_tones[0]}_streak"

    # Create emotion context string and inject into core_facts for Brain
    emotion_context = get_emotion_context_string(
        emotional_state,
        merged_context.get("is_owner", False),
    )
    existing_facts = list(persistent_core_facts)
    existing_facts.insert(0, emotion_context)
    if _absence_fact:
        existing_facts.insert(1, _absence_fact)
    existing_facts.append(_time_fact)
    existing_facts.append(_energy_fact)
    existing_facts.append(_conv_depth_fact)
    if _session_drain_fact:
        existing_facts.append(_session_drain_fact)
    if _thaw_fact:
        existing_facts.append(_thaw_fact)
    if _ambiv_fact:
        existing_facts.append(_ambiv_fact)
    if _withdrawal_fact:
        existing_facts.append(_withdrawal_fact)
    if _mood_momentum_fact:
        existing_facts.append(_mood_momentum_fact)
    # Active global modifiers — Koroki's own condition affecting all interactions
    for _mod_label in _active_modifiers:
        existing_facts.append(f"koroki_condition={_mod_label}")
    # Topic interest — scan user's message for things Koroki genuinely cares about
    if not req.proactive and not req.game_event_context:
        _topic_fact = build_topic_fact(req.message)
        if _topic_fact:
            existing_facts.append(_topic_fact)
    # Sight percept — what she sees in the image(s) attached to this message.
    # Front of the fact list (same salience as game commentary): the small brain
    # ignores late facts. Plus an imperative nudge to actually voice it — without
    # it she acknowledges the image exists but never engages with the content
    # (owner, 2026-07-03). Own words, not verbatim — she glanced, didn't read a report.
    if _vision_desc:
        existing_facts.insert(1, f"you_see_the_image_they_sent: {_vision_desc[:400]}")
        existing_facts.insert(
            2,
            "start_by_naming_what_the_image_shows_in_your_own_words_then_give_your_take;"
            "never_recite_the_description",
        )
    elif _vision_blind:
        existing_facts.append(
            "they_attached_an_image_but_your_vision_service_is_offline; "
            "you_cannot_see_it_right_now"
        )
    # Minimal message signal — very short inputs (≤3 words, no question) often mean
    # the user is distracted, testing, or wants brevity mirrored back.
    _msg_word_count = len(req.message.split())
    if _msg_word_count <= 3 and "?" not in req.message and not req.proactive:
        existing_facts.append("message_is_minimal=true;user_sent_very_few_words")
    # Opener freshness — on the very first turn of a session, let the model know what
    # emotion she led with last time so she doesn't open every conversation identically.
    if _turn_count <= 1 and not req.proactive:
        _last_opener = str(cached_user_payload.get("last_opener_emotion") or "").strip()
        if _last_opener and _last_opener != "neutral":
            existing_facts.append(f"last_opener_emotion={_last_opener};vary_your_opening_register")
    merged_context["core_facts"] = existing_facts[: max_core_facts]
    
    logger.info(
        "[%s] Emotional state: emotion=%s intensity=%d (base=%s)",
        req.request_id,
        emotional_state.current_emotion,
        emotional_state.intensity,
        emotional_state.base_emotion,
    )
    emotion_hints = get_tts_hints(
        emotional_state,
        is_owner=bool(merged_context.get("is_owner", False)),
        relationship_score=int(merged_context.get("relationship_score", req.user_context.relationship_score)),
    )
    timing.mark("cognitive_observe")

    cognitive_snapshot = None
    if cognition_enabled:
        cognitive_snapshot = run_cognitive_cycle(
            user_id=req.user_context.user_id,
            user_message=req.message,
            merged_context=merged_context,
            emotional_state=emotional_state.to_dict(),
        )
        if cognition_runtime_context_enabled and cognition_max_runtime_facts > 0:
            runtime_facts = cognitive_snapshot.to_runtime_facts()[:cognition_max_runtime_facts]
            existing_facts = list(merged_context.get("core_facts") or [])
            existing_facts = runtime_facts + existing_facts
            merged_context["core_facts"] = existing_facts[:max_core_facts]
    timing.mark("cognitive_evaluate")

    memory_rag_enabled = bool(memory_cfg.get("memory_rag_enabled", True))
    if memory_rag_enabled:
        memory_facts = build_memory_rag_facts(cached_user_payload, max_facts=int(memory_cfg.get("memory_rag_max_facts", 6)))
        if memory_facts:
            existing_facts = list(merged_context.get("core_facts") or [])
            dedup = set(existing_facts)
            for fact in memory_facts:
                if fact not in dedup:
                    existing_facts.insert(0, fact)
                    dedup.add(fact)
            merged_context["core_facts"] = existing_facts[:max_core_facts]

        # Active boredom redirect: passive satiation fact alone doesn't drive the model to act.
        # When a topic dominates recent episodes, inject a directive so Koroki feels the
        # pull to shift — the way a real person gets antsy when a subject runs too long.
        _boredom_topic: str | None = None
        for _mf in memory_facts:
            if str(_mf).startswith("topic_recurring_heavily="):
                _boredom_topic = str(_mf).split("=", 1)[1].strip()
                break
        if _boredom_topic:
            _boredom_hint = (
                f"koroki_boredom_signal={_boredom_topic}_recurs_often;"
                f"you feel the pull to redirect or let this thread rest naturally"
            )
            existing_facts = list(merged_context.get("core_facts") or [])
            existing_facts.append(_boredom_hint)
            merged_context["core_facts"] = existing_facts[:max_core_facts]

    logger.info(
        "[%s] Emotion TTS hints: primary=%s secondary=%s warmth=%d arousal=%d dominance=%d variant=%s",
        req.request_id,
        emotion_hints.primary_tag,
        emotion_hints.secondary_tags,
        emotion_hints.warmth,
        emotion_hints.arousal,
        emotion_hints.dominance,
        emotion_hints.suggested_variant,
    )
    
    if mention_bridge_enabled and mention_fact_limit > 0 and not is_web_platform:
        mentioned_user_ids = _extract_mentioned_user_ids(
            req.message,
            req.user_context.mentioned_user_ids,
        )
        mention_facts, newly_known = await _build_mention_bridge_facts(
            req.user_context.user_id,
            mentioned_user_ids,
            mention_fact_limit,
        )
        if mention_facts:
            existing_facts = list(merged_context.get("core_facts") or [])
            dedup = set(existing_facts)
            for fact in mention_facts:
                if fact not in dedup:
                    existing_facts.append(fact)
                    dedup.add(fact)
            merged_context["core_facts"] = existing_facts[:max_core_facts]
        if newly_known:
            known_set = {str(uid) for uid in known_users}
            for uid in newly_known:
                if uid not in known_set:
                    known_users.append(uid)
                    known_set.add(uid)

    logger.info(
        "[%s] Memory load: cached=%s owner=%s relationship_score=%s recent_turns=%d core_facts=%d known_users=%d",
        req.request_id,
        bool(cached_user_payload),
        merged_context.get("is_owner", False),
        merged_context.get("relationship_score", req.user_context.relationship_score),
        len(merged_context.get("recent_turns") or []),
        len(merged_context.get("core_facts") or []),
        len(known_users),
    )
    timing.mark("memory_fetch")
    
    # === Inject RAG facts for current events and web knowledge ===
    rag_enabled = bool(memory_cfg.get("rag_enabled", True)) and not is_web_platform and not req.proactive
    existing_facts = list(merged_context.get("core_facts") or [])
    enriched_facts = await inject_rag_facts(
        req.message,
        core_facts=existing_facts,
        max_facts=2,
        rag_enabled=rag_enabled,
    )
    merged_context["core_facts"] = enriched_facts[:max_core_facts]
    if len(enriched_facts) > len(existing_facts):
        logger.info("[%s] RAG injected %d fact(s)", req.request_id, len(enriched_facts) - len(existing_facts))

    # Game event context: inject streamer commentary directive ahead of all other facts
    # (watch/play path — the scene rides the message, the directive rides the facts).
    _proactive_signal: str | None = None
    if req.game_event_context and not req.proactive:
        existing_facts = list(merged_context.get("core_facts") or [])
        existing_facts.insert(0, req.game_event_context)
        merged_context["core_facts"] = existing_facts[:max_core_facts]
    elif req.proactive:
        # Internal reach-out. The bot sends a placeholder message ("..."/".") — that
        # must NEVER reach the model: she reads the final turn as a person speaking
        # and will argue with three dots ("you're being vague", 2026-07-04). Instead
        # the final turn becomes an honest [system] signal — her own scheduler,
        # declared as such in the brain's core prompt. Topic anchor + unanswered-
        # streak awareness live inside the signal; game_event_context (idle outreach)
        # overrides the default body.
        _proactive_signal = build_proactive_signal(
            cached_user_payload, override_context=req.game_event_context
        )

    # === Inject current thought — Koroki's persistent internal state ===
    # Changes hourly. Gives the model something real to draw from instead of
    # defaulting to trained templates. Always present so "what are you thinking?"
    # gets an honest answer. Skip for proactive/game modes (already anchored).
    if not req.proactive and not req.game_event_context:
        _thought = get_current_thought()
        _thought_facts = list(merged_context.get("core_facts") or [])
        _thought_facts = [f for f in _thought_facts if not str(f or "").startswith("current_thought=")]
        _thought_facts.append(f"current_thought={_thought}")
        _thought_facts.append(build_state_block(get_state()))
        _thought_facts.append(build_capability_block())
        # Self-history: inject when the message touches identity / introspection topics
        _rel_score = int(merged_context.get("relationship_score", req.user_context.relationship_score))
        if should_inject_history(req.message, _rel_score, bool(merged_context.get("is_owner", False))):
            _history_block = get_history_block()
            if _history_block:
                _thought_facts.append(_history_block)
        # Pending relationship milestone: inject once, then clear it from memory next save
        _pending_ms = cached_user_payload.get("pending_milestone")
        if _pending_ms and isinstance(_pending_ms, dict):
            _from = _pending_ms.get("from_tier", "")
            _to = _pending_ms.get("to_tier", "")
            _thought_facts.append(
                f"relationship_milestone=tier_crossed | from={_from} to={_to} | "
                "This person just crossed into a new tier of closeness with you. "
                "You don't announce it. But something in how you respond to them "
                "shifts slightly — warmer, a little more open, a little less guarded. Natural, not stated."
            )
            merged_context["pending_milestone"] = None  # clear after injection
        merged_context["core_facts"] = _thought_facts[:max_core_facts]

    timing.mark("prompt_build")

    slot = create_inference_slot(
        request_id=req.request_id,
        user_id=req.user_context.user_id,
        mode=merged_context.get("mode", req.user_context.mode),
        relationship_score=merged_context.get("relationship_score", req.user_context.relationship_score),
        pre_generation_enabled=pre_generation_enabled,
    )

    # --- One-call protocol: stream Brain text, then synthesize the full filtered reply once ---
    _budget_circ = circadian_affect_overlay(_utc7_now)
    _budget_fatigue = max(0, min(100,
        emotional_state.affect_vector.get("fatigue", 50) + _budget_circ.get("fatigue", 0)
    ))
    _budget_playfulness = max(0, min(100,
        emotional_state.affect_vector.get("playfulness", 50) + _budget_circ.get("playfulness", 0)
    ))
    brain_max_new_tokens = (
        80 if req.game_event_context  # game commentary: enough for move notation + reaction
        else 56 if req.proactive  # 1-2 sentences; 32 guillotined reach-outs mid-thought
        else _emotion_token_budget(
            base=web_max_new_tokens if is_web_platform else discord_max_new_tokens,
            emotion_label=emotional_state.current_emotion,
            fatigue=_budget_fatigue,
            playfulness=_budget_playfulness,
        )
    )
    _thinking_enabled = _should_enable_thinking(
        req.message,
        is_owner=bool(merged_context.get("is_owner", False)),
        relationship_score=int(merged_context.get("relationship_score", req.user_context.relationship_score)),
    )
    # Thinking mode burns tokens internally (Ollama strips the <think> block from output
    # but still charges them against num_predict). Multiply budget so the visible response
    # isn't starved after the think block consumes most of the budget.
    if _thinking_enabled:
        brain_max_new_tokens = min(brain_max_new_tokens * 4, 512)
    # Image replies get a floor: saying what she sees + her take needs room even
    # when fatigue/mood would otherwise shrink the budget.
    if _vision_desc:
        brain_max_new_tokens = max(brain_max_new_tokens, 96)
    _forbidden_phrases = _detect_repeated_phrases(list(merged_context.get("recent_turns") or []))
    if _forbidden_phrases:
        logger.info("[%s] Cross-turn repeat detected — forbidding %d phrase(s): %s",
                    req.request_id, len(_forbidden_phrases), _forbidden_phrases)

    # ── Phase 1 + 2A + 2B body / mind integration ────────────────────────
    # Pipeline:
    #   1. Note user activity in presence tracker (drives sustained_presence events).
    #   2. Derive an Event from emotional_state, ingest into endocrine.
    #   3. Emit any sustained_presence events that crossed thresholds — they
    #      feed back into endocrine as `affectionate, owner_present` events.
    #   4. Retrieve relevant memories — fires recall feedback into body.
    #   5. Auto-write a memory if body says this moment was important enough.
    #   6. Generate felt-state and inject into brain payload.
    _felt_state_block: str | None = None
    try:
        _av = emotional_state.affect_vector
        _is_owner = bool(merged_context.get("is_owner", False))
        _user_id = req.user_context.user_id

        # Step 1: note activity (presence + energy + sleep arousal).
        get_presence().note_activity(_user_id, is_owner=_is_owner)
        get_energy().note_interaction()
        get_sleep().external_arousal_event()  # may wake her from light sleep
        # Ensure sleep cycle is wired so memory consolidation fires on sleep transitions.
        get_sleep_cycle()

        # Step 1b: Phase 3 — apply emotional residue from prior sessions with this user.
        # Inject any decayed residue back into endocrine BEFORE the rest of this turn
        # so the body starts the session shaped by how it felt last time.
        try:
            _rel = get_relationships().get_or_create(_user_id, is_owner=_is_owner)
            get_residue().apply_residue_to_endocrine(
                _user_id, get_endocrine().ingest_event, BodyEvent, is_owner=_is_owner
            )
        except Exception as _phase3_exc:
            logger.debug("[%s] Phase 3 residue apply skipped: %s",
                          req.request_id, _phase3_exc)

        # Step 2: classify the message into a body-relevant event.
        _pos_emotions = {"caring", "playful", "happy", "warm", "curious", "affectionate", "content"}
        _neg_emotions = {"sad", "anxious", "irritated", "fearful", "angry", "hurt", "lonely"}
        _emo = (emotional_state.current_emotion or "neutral").lower()
        _valence = 0.0
        if _emo in _pos_emotions:
            _valence = min(1.0, (emotional_state.intensity or 50) / 100.0)
        elif _emo in _neg_emotions:
            _valence = -min(1.0, (emotional_state.intensity or 50) / 100.0)
        _intensity = max(0.1, min(1.0, (_av.get("arousal", 50) or 50) / 100.0))
        _tags: list[str] = []
        if _is_owner:
            _tags.append("owner_present")
        if _emo in {"caring", "affectionate", "warm"}:
            _tags.append("affectionate")
        if _emo in {"irritated", "angry"}:
            _tags.append("conflict")
        if _emo in {"curious", "playful"}:
            _tags.append("novelty")
        get_endocrine().ingest_event(BodyEvent(
            type=f"user_message:{_emo}",
            source=_user_id,
            valence=_valence,
            intensity=_intensity,
            tags=_tags,
        ))

        # Phase 3: update relationship + write residue based on this event.
        try:
            _rel_mgr = get_relationships()
            if "affectionate" in _tags or _valence > 0.5:
                _rel_mgr.note_warm_interaction(_user_id, is_owner=_is_owner)
                get_residue().write_residue(_user_id, "oxytocin", 0.03,
                                              summary_hint=f"warm:{_emo}")
            elif "conflict" in _tags or _valence < -0.5:
                _rel_mgr.note_conflict(_user_id, is_owner=_is_owner)
                get_residue().write_residue(_user_id, "cortisol", 0.05,
                                              summary_hint=f"conflict:{_emo}")
            else:
                _rel_mgr.note_neutral_interaction(_user_id, is_owner=_is_owner)
        except Exception as _rel_exc:
            logger.debug("[%s] Phase 3 relationship update skipped: %s",
                          req.request_id, _rel_exc)

        # Step 3: emit any sustained_presence events that crossed thresholds.
        for sp_event in get_presence().maybe_emit_sustained_events():
            # Only owner's sustained presence drives oxytocin baseline up here;
            # non-owners just get the soft event for memory.
            sp_tags = ["sustained_presence"]
            if sp_event.is_owner:
                sp_tags.extend(["owner_present", "affectionate"])
            get_endocrine().ingest_event(BodyEvent(
                type="sustained_presence",
                source=sp_event.user_id,
                valence=0.4 if sp_event.is_owner else 0.2,
                intensity=sp_event.intensity,
                tags=sp_tags,
            ))

        # Step 4: retrieve relevant memories — fires recall feedback to body AND puts the
        # recalled content in front of the captain. (Until 2026-07-03 the recall ONLY fed
        # hormones: she'd feel warm about a memory she couldn't see — semantic recall
        # retrieved "cockatiel" at 0.845 and she still answered blind. Subsystem recalls,
        # captain READS — both halves are required.)
        try:
            # Sight-enriched query: an attached image's content should pull
            # related memories too ("remember that screenshot?").
            memory_results = get_memory().retrieve(_message_with_sight, top_k=3)
            for r in memory_results:
                get_memory().apply_recall_feedback(
                    r.node, get_endocrine().ingest_event, BodyEvent
                )
            _recall_facts = []
            for r in memory_results:
                if r.score_relevance < 0.30:
                    continue
                _content = r.node.content.strip()
                # don't echo the message she's answering right now
                if req.message[:120] in _content:
                    continue
                _recall_facts.append(f"you_remember: {_content[:180]}")
                if len(_recall_facts) >= 2:
                    break
            if _recall_facts:
                _cf = list(merged_context.get("core_facts") or [])
                merged_context["core_facts"] = _cf + _recall_facts
                logger.info("[%s] Recalled memories injected: %d", req.request_id,
                            len(_recall_facts))
        except Exception as _mem_recall_exc:
            logger.debug("[%s] Memory recall feedback skipped: %s",
                          req.request_id, _mem_recall_exc)

        # Step 5: maybe write THIS moment to memory (importance from current body).
        try:
            _current_body = {n: c.level for n, c in get_endocrine().components.items()}
            _mem_node = get_memory().remember(
                content=f"[{_emo}] {_message_with_sight[:300]}",
                body_state=_current_body,
                source_user_id=_user_id,
                tags=_tags,
            )
            # Interest drift: a topic that was live while her body actually activated
            # gets reinforced — tastes grow from lived experience (causal chain).
            if _mem_node is not None:
                try:
                    from ..topic_interests import analyze_message as _ti_analyze
                    from ..topic_interests import base_weight as _ti_base
                    from ..mind.interest_drift import get_interest_drift
                    _topic, _w, _valence = _ti_analyze(req.message)
                    if _topic and _valence == "positive":
                        get_interest_drift().reinforce(
                            _topic, _ti_base(_topic), _mem_node.importance)
                except Exception:
                    logger.debug("[%s] interest drift skipped", req.request_id, exc_info=True)
        except Exception as _mem_write_exc:
            logger.debug("[%s] Memory write skipped: %s",
                          req.request_id, _mem_write_exc)

        # Step 6: assemble felt-state snapshot for the captain LLM.
        _felt_state = get_felt_state()
        _felt_state_block = _felt_state.to_prompt_block() or None
        if _felt_state_block:
            logger.info(
                "[%s] Felt state: body=%r mind=%r mood=%r ctx=%r raw=%s",
                req.request_id, _felt_state.body, _felt_state.mind,
                _felt_state.mood, _felt_state.context, _felt_state._raw,
            )
    except Exception as _body_exc:
        # Body subsystems are new and shouldn't break chat — log and continue.
        logger.warning("[%s] Body/mind integration failed (non-fatal): %s",
                       req.request_id, _body_exc)

    # The captain reads the MESSAGE far harder than core_facts — a small brain
    # deflects on percepts that live only in the fact list. So the sight percept
    # rides inline with the message she's answering (owner: she must voice what
    # she sees, in her own words, not recite it).
    _brain_message = (
        f"{req.message}\n[you look at the image they attached — you see: {_vision_desc[:400]}]"
        if _vision_desc
        else req.message
    )
    if _proactive_signal:
        _brain_message = _proactive_signal

    brain_payload = {
        "request_id": req.request_id,
        "message": _brain_message,
        "user_context": merged_context,
        "max_new_tokens": brain_max_new_tokens,
        "enable_thinking": _thinking_enabled,
        "forbidden_phrases": _forbidden_phrases,
        "felt_state": _felt_state_block,
    }
    logger.info(
        "[%s] Brain token budget: platform=%s max_new_tokens=%d rag_enabled=%s mention_bridge=%s",
        req.request_id,
        "web" if is_web_platform else "discord_like",
        brain_max_new_tokens,
        rag_enabled,
        bool(mention_bridge_enabled and mention_fact_limit > 0 and not is_web_platform),
    )
    timing.mark("cognitive_plan")

    brain_ws_url = (
        settings["services"]["brain"]["url"]
        .replace("http://", "ws://")
        .replace("https://", "wss://")
        + "/ws/stream"
    )
    brain_svc_cfg = settings.get("services", {}).get("brain", {})
    ws_open_timeout_s = float(brain_svc_cfg.get("ws_open_timeout_s", 30.0))
    ws_ping_interval_s = float(brain_svc_cfg.get("ws_ping_interval_s", 20.0))
    ws_ping_timeout_s = float(brain_svc_cfg.get("ws_ping_timeout_s", 120.0))

    # --- Sentence-streaming TTS (flag-gated): synthesize sentence N while the Brain
    # writes N+1 — perceived-latency win of 30-50% on multi-sentence replies. The audio
    # is only used when the streamed text survives post-processing unchanged; any repair
    # (crutch retry / repetition trim / think strip) or segment failure falls back to the
    # classic one-call TTS below. See pipeline/sentence_stream.py for the pause model.
    # Streaming-mode voice tradeoffs (documented): cross-sentence cue injections
    # (auto/explicit/sentence tags, period-pause softening) are skipped — the IndexTTS
    # emo_vector remains the primary emotion channel.
    _stream_speech: StreamingSpeech | None = None
    _ss_cfg = settings.get("features", {}).get("sentence_streaming", {})
    _ss_indextts_url = settings["services"]["tts"].get("adapter_url", "")
    if bool(_ss_cfg.get("enabled", False)) and _ss_indextts_url and not req.defer_tts:
        _ss_circ = circadian_affect_overlay(_utc7_now)
        _ss_affect = {
            k: max(0, min(100, emotional_state.affect_vector.get(k, 50) + _ss_circ.get(k, 0)))
            for k in emotional_state.affect_vector
        }
        _ss_fatigue = _ss_affect.get("fatigue", 50)
        _ss_rel_score = merged_context.get("relationship_score", req.user_context.relationship_score)
        _ss_emo_vector = affect_to_emo_vector(
            affect_vector=_ss_affect,
            emotion_label=emotional_state.current_emotion,
            emotion_intensity=emotional_state.intensity,
            relationship_score=_ss_rel_score,
        )
        _ss_counter = {"n": 0}

        def _ss_shaper(raw: str, is_last: bool) -> str:
            # per-sentence mirror of the classic shaping chain (single-sentence-safe ops)
            s = _strip_tts_actions(raw)
            s = _sanitize_tts_speech_text(s)
            s = _inject_disfluency(
                s, emotion_label=emotional_state.current_emotion, fatigue=_ss_fatigue)
            s = _inject_breath_filler(
                s, emotion_label=emotional_state.current_emotion,
                fatigue=_ss_fatigue, intensity=emotional_state.intensity)
            _ss_cl, _ss_mp = _tts_rhythm_params(
                emotional_state.current_emotion, fatigue=_ss_fatigue)
            if _tts_repair_layer_enabled("pacing_shaping_enabled", True):
                s = _shape_tts_pacing_text(
                    s, long_clause_word_threshold=_ss_cl, max_pause_insertions=_ss_mp)
            s = _normalize_tts_surface_text(s)
            if is_last:
                s = _stabilize_tts_tail(s)
            return s.strip()

        async def _ss_synth(shaped: str) -> bytes | None:
            _ss_counter["n"] += 1
            _ss_payload = {
                "request_id": f"{req.request_id}_s{_ss_counter['n']}",
                "text": shaped,
                "relationship_score": _ss_rel_score,
                "emotion": emotional_state.current_emotion,
                "emotion_intensity": emotional_state.intensity,
                "emotion_variant": emotion_hints.suggested_variant,
                "emo_vector": _ss_emo_vector,
            }
            try:
                async with httpx.AsyncClient(timeout=120.0) as _ss_client:
                    _ss_r = await _ss_client.post(
                        f"{_ss_indextts_url}/synthesize", json=_ss_payload)
                    _ss_r.raise_for_status()
                    return base64.b64decode(_ss_r.json()["wav_base64"])
            except Exception as _ss_exc:
                logger.warning("[%s] sentence-stream segment synth failed: %s",
                               req.request_id, _ss_exc)
                return None

        # min_sentence_chars=12: ultra-short fragments merge into the next sentence —
        # avoids the IndexTTS short-input runaway AND reduces audible seams
        _stream_speech = StreamingSpeech(
            synth=_ss_synth, shaper=_ss_shaper, max_chars=max_tts_chars,
            min_sentence_chars=12)
        logger.info("[%s] Sentence-streaming TTS active", req.request_id)

    guillotine = StreamingGuillotine()
    text_parts: list[str] = []
    adapter_used = "unknown"
    try:
        async with websockets.connect(
            brain_ws_url,
            max_size=2**20,
            open_timeout=ws_open_timeout_s,
            ping_interval=ws_ping_interval_s,
            ping_timeout=ws_ping_timeout_s,
        ) as ws:
            await ws.send(json.dumps(brain_payload))

            while True:
                raw = await ws.recv()
                message = json.loads(raw)
                message_type = message.get("type")

                if message_type == "text_chunk":
                    chunk = message.get("text", "")
                    if not chunk:
                        continue

                    adapter_used = message.get("adapter_used", adapter_used)
                    timing.mark_token()
                    text_parts.append(chunk)
                    slot.append_text(chunk)

                    try:
                        guillotine.scan(chunk)
                    except GuillotineViolation as gv:
                        logger.error("[%s] Guillotine blocked streamed chunk: %s", req.request_id, gv)
                        if _stream_speech is not None:
                            _stream_speech.cancel()
                        raise HTTPException(status_code=500, detail="Response blocked by safety filter.")

                    if _stream_speech is not None:
                        _stream_speech.feed(chunk)

                    continue

                if message_type == "terminate":
                    logger.error(
                        "[%s] Brain terminate signal: code=%s pattern=%s",
                        req.request_id,
                        message.get("code"),
                        message.get("pattern"),
                    )
                    if _stream_speech is not None:
                        _stream_speech.cancel()
                    raise HTTPException(status_code=500, detail="Brain stream terminated by safety guard.")

                if message_type == "error":
                    if _stream_speech is not None:
                        _stream_speech.cancel()
                    raise HTTPException(
                        status_code=502,
                        detail=f"Brain stream error: {message.get('error', 'unknown')}",
                    )

                if message_type == "complete":
                    adapter_used = message.get("adapter_used", adapter_used)
                    _tool_calls = message.get("tool_calls") or []
                    if _tool_calls:
                        _extra_facts = execute_tool_calls(
                            _tool_calls,
                            emotional_state=emotional_state,
                            merged_context=merged_context,
                            request_id=req.request_id,
                        )
                        # Append explicitly stored memories to persistent core facts
                        for _fact in _extra_facts:
                            _content = _fact.get("content", "")
                            _importance = _fact.get("importance", 50)
                            if _content:
                                _fact_str = f"koroki_memory(importance={_importance}): {_content}"
                                persistent_core_facts.append(_fact_str)
                    break
    except TimeoutError as exc:
        timing.finalize()
        if _stream_speech is not None:
            _stream_speech.cancel()
        logger.error(
            "[%s] Brain websocket open timeout after %ss: %s",
            req.request_id,
            ws_open_timeout_s,
            exc,
        )
        raise HTTPException(status_code=502, detail="Brain stream unavailable: timed out during opening handshake")
    except (OSError, websockets.WebSocketException) as exc:
        timing.finalize()
        if _stream_speech is not None:
            _stream_speech.cancel()
        logger.error("[%s] Brain websocket error: %s", req.request_id, exc)
        raise HTTPException(status_code=502, detail=f"Brain stream unavailable: {exc}")

    response_text = "".join(text_parts).strip()
    response_text, think_removed = _strip_thinking_artifacts(response_text)
    if think_removed:
        logger.warning("[%s] Think leak guard removed hidden-thinking artifacts", req.request_id)
    repetition_trimmed = False
    repetition_ratio: float | None = None
    if repetition_tail_trim_enabled:
        response_text, repetition_trimmed, repetition_ratio = _trim_repetitive_tail(response_text)
        if repetition_trimmed:
            logger.info(
                "[%s] Repetition guard trimmed response tail (ratio=%.3f)",
                req.request_id,
                repetition_ratio if repetition_ratio is not None else -1.0,
            )

    # Crutch-phrase retry: if the model produced a known template as the whole response,
    # make one non-streaming retry with that phrase explicitly forbidden so it must vary.
    # Only fires when the non-action content is ≤80 chars (i.e. the crutch IS the response).
    _crutch_matched = _detect_crutch_phrase(response_text)
    if _crutch_matched:
        logger.warning(
            "[%s] Crutch phrase detected (%r) — retrying with it forbidden",
            req.request_id,
            _crutch_matched,
        )
        _retry_payload = dict(brain_payload)
        _retry_payload["request_id"] = req.request_id + "_retry"
        # Forbid the full set of known crutch forms, not just the detected one — prevents
        # the model from falling back to an alternative template on retry.
        _retry_forbidden = list(set(
            (brain_payload.get("forbidden_phrases") or [])
            + [_crutch_matched]
            + _CRUTCH_CANONICAL_PHRASES
        ))
        _retry_payload["forbidden_phrases"] = _retry_forbidden
        # Deep-copy user_context and inject a concrete internal anchor so the model has
        # something specific to respond FROM instead of defaulting to a template.
        _retry_ctx = copy.deepcopy(_retry_payload.get("user_context") or {})
        _retry_anchor = get_current_thought()
        _retry_ctx_facts = list(_retry_ctx.get("core_facts") or [])
        _retry_ctx_facts.append(f"crutch_retry_anchor={_retry_anchor}")
        _retry_ctx["core_facts"] = _retry_ctx_facts
        _retry_payload["user_context"] = _retry_ctx
        _brain_http_url = settings["services"]["brain"]["url"] + "/v1/generate"
        try:
            async with httpx.AsyncClient(timeout=60.0) as _rc:
                _rr = await _rc.post(_brain_http_url, json=_retry_payload)
                _rr.raise_for_status()
                _retry_text = (_rr.json().get("text") or "").strip()
                _retry_text, _ = _strip_thinking_artifacts(_retry_text)
                if _retry_text and not _detect_crutch_phrase(_retry_text):
                    response_text = _retry_text
                    logger.info("[%s] Crutch retry succeeded: %r", req.request_id, response_text[:80])
                else:
                    logger.warning("[%s] Crutch retry also produced template — keeping original", req.request_id)
        except Exception as _retry_exc:
            logger.warning("[%s] Crutch retry failed: %s — keeping original", req.request_id, _retry_exc)

    # Cross-turn repetition check: warn if this response is too similar to a recent one.
    # The prompt-level "Do Not Repeat" block should prevent this; this is an observability signal.
    _recent_assistant_turns = [
        t.get("content", "")
        for t in (merged_context.get("recent_turns") or [])
        if t.get("role") == "assistant" and t.get("content", "").strip()
    ][-4:]
    _response_norm = _normalize_clause(response_text)
    for _prior in _recent_assistant_turns:
        _prior_norm = _normalize_clause(_prior)
        if len(_prior_norm) < 10:
            continue
        _sim = SequenceMatcher(None, _response_norm, _prior_norm).ratio()
        if _sim >= 0.75:
            logger.warning(
                "[%s] Cross-turn repetition detected (sim=%.2f) — response too similar to prior: %r",
                req.request_id,
                _sim,
                _prior[:80],
            )
            break

    # === Emotion feedback loop — Phase 1: divergence detection + preference logging ===
    _is_owner_ctx = bool(merged_context.get("is_owner", False))
    _expressed_emotion = _classify_text_emotion(response_text, _is_owner_ctx)
    _intended_emotion = emotional_state.current_emotion
    _emotion_diverged = (
        _expressed_emotion is not None
        and _expressed_emotion != _intended_emotion
        and not (
            _expressed_emotion in {"caring", "playful", "protective"}
            and _intended_emotion in {"caring", "playful", "protective"}
        )
    )
    if _emotion_diverged:
        logger.warning(
            "[%s] Emotion divergence: intended=%s expressed=%s",
            req.request_id, _intended_emotion, _expressed_emotion,
        )
    else:
        logger.debug(
            "[%s] Emotion coherence: intended=%s expressed=%s",
            req.request_id, _intended_emotion, _expressed_emotion,
        )
    _reflection_issues = _reflect_on_response(
        response_text,
        emotion=_intended_emotion,
        relationship_score=int(merged_context.get("relationship_score", req.user_context.relationship_score)),
        is_owner=_is_owner_ctx,
    )
    if _reflection_issues:
        logger.warning(
            "[%s] Reflection issues: %s",
            req.request_id, ", ".join(_reflection_issues),
        )

    asyncio.create_task(preference_logger.log_response(
        request_id=req.request_id,
        user_id=req.user_context.user_id,
        user_message=req.message,
        response_text=response_text,
        intended_emotion=_intended_emotion,
        expressed_emotion=_expressed_emotion,
        relationship_score=int(merged_context.get(
            "relationship_score", req.user_context.relationship_score
        )),
        is_owner=_is_owner_ctx,
    ))

    reflective_snapshot = cognitive_snapshot
    if cognition_enabled:
        reflective_snapshot = run_cognitive_cycle(
            user_id=req.user_context.user_id,
            user_message=response_text,
            merged_context=merged_context,
            emotional_state=emotional_state.to_dict(),
        )
        timing.set_cognitive_metrics(
            coherence=reflective_snapshot.coherence_score,
            affect_stability=reflective_snapshot.affect_stability,
            memory_coherence=reflective_snapshot.memory_coherence,
            intent_strength=reflective_snapshot.intent_strength,
            attention_entropy=reflective_snapshot.attention_entropy,
            initiative_drive=reflective_snapshot.initiative_drive,
            reflection_score=reflective_snapshot.reflection_score,
            proactive_eligible=reflective_snapshot.proactive_eligible,
            proactive_cooldown_s=reflective_snapshot.proactive_cooldown_s,
        )
    timing.mark("cognitive_reflect")

    # --- Single TTS call: preserve emotional tone cues for speech, clip to budget ---
    response_text = _preprocess_tts_text(response_text)
    stripped_plain = _strip_tts_actions(response_text)
    tts_chars_filtered = max(0, len(response_text) - len(stripped_plain))
    if tts_emotion_cues_enabled:
        emotionized_text, tts_emotion_cues_added = _inject_tts_emotion_cues(
            response_text,
            max_cues=max(0, max_tts_emotion_cues),
            style=tts_emotion_cue_style,
        )
    else:
        emotionized_text, tts_emotion_cues_added = stripped_plain, 0

    explicit_tags_applied = 0
    explicit_tags_seen: list[str] = []
    auto_tags_inferred: list[str] = []
    sentence_tags: list[str] = []

    if tts_auto_emotion_tags_enabled:
        auto_tags_inferred = _infer_auto_emotion_tags(
            response_text,
            is_owner=bool(merged_context.get("is_owner", False)),
            relationship_score=int(merged_context.get("relationship_score", req.user_context.relationship_score)),
            emotion_hints=emotion_hints,
            max_tags=tts_max_auto_emotion_tags,
        )
        emotionized_text = _inject_auto_emotion_tags(emotionized_text, auto_tags_inferred)

    if tts_explicit_emotion_tags_enabled:
        emotionized_text, explicit_tags_applied, explicit_tags_seen = _apply_explicit_emotion_tags(
            emotionized_text,
            max_tags=tts_max_explicit_emotion_tags,
            render_cues=False,
        )

    if tts_sentence_emotion_cues_enabled:
        sentence_tags = _infer_sentence_emotion_tags(
            emotionized_text,
            is_owner=bool(merged_context.get("is_owner", False)),
            relationship_score=int(merged_context.get("relationship_score", req.user_context.relationship_score)),
            emotion_hints=emotion_hints,
            max_sentences=6,
        )
        emotionized_text = _apply_sentence_emotion_cues(
            emotionized_text,
            sentence_tags,
            max_verbal_cues=1,
        )

    # Circadian overlay — ephemeral, applied only for this request's TTS/rhythm.
    _circ = circadian_affect_overlay(_utc7_now)
    _tts_affect = {
        k: max(0, min(100, emotional_state.affect_vector.get(k, 50) + _circ.get(k, 0)))
        for k in emotional_state.affect_vector
    }
    _tts_fatigue = _tts_affect.get("fatigue", emotional_state.affect_vector.get("fatigue", 50))

    speech_text = _sanitize_tts_speech_text(emotionized_text)
    speech_text = _inject_disfluency(
        speech_text,
        emotion_label=emotional_state.current_emotion,
        fatigue=_tts_fatigue,
    )
    speech_text = _inject_breath_filler(
        speech_text,
        emotion_label=emotional_state.current_emotion,
        fatigue=_tts_fatigue,
        intensity=emotional_state.intensity,
    )
    _rhythm_clause_threshold, _rhythm_max_pauses = _tts_rhythm_params(
        emotional_state.current_emotion,
        fatigue=_tts_fatigue,
    )
    if tts_pacing_shaping_enabled:
        speech_text = _shape_tts_pacing_text(
            speech_text,
            long_clause_word_threshold=_rhythm_clause_threshold,
            max_pause_insertions=_rhythm_max_pauses,
        )
    if tts_period_pause_softening_enabled:
        speech_text = _soften_tts_period_pauses(
            speech_text,
            max_replacements=tts_period_pause_softening_max,
        )
    speech_text = _normalize_tts_surface_text(speech_text)
    clipped_speech = _clip_tts_text(speech_text, max_tts_chars)
    clipped_speech = _stabilize_tts_tail(clipped_speech)
    clipped_speech = _normalize_tts_surface_text(clipped_speech)
    tts_spoken_chars = len(clipped_speech)
    tts_shaping_requested = bool(
        tts_action_stripping_enabled
        or tts_emotion_cues_enabled
        or tts_auto_emotion_tags_enabled
        or tts_explicit_emotion_tags_enabled
        or tts_sentence_emotion_cues_enabled
        or tts_pacing_shaping_enabled
        or tts_period_pause_softening_enabled
        or tts_sanitize_speech_enabled
    )

    tts_audio: bytes | None = None
    tts_request_payload: dict | None = None
    if clipped_speech:
        tts_text_hash = hashlib.sha1(clipped_speech.encode("utf-8", errors="ignore")).hexdigest()[:12]
        tts_preview = clipped_speech[:96].replace("\n", " ")
        logger.info(
            "[%s] TTS one-call: spoken_chars=%d filtered_chars=%d emotion_cues_added=%d auto_tags_inferred=%s sentence_tags=%s explicit_tags_applied=%d explicit_tags_seen=%s cue_style=%s text_hash=%s preview=%r",
            req.request_id,
            tts_spoken_chars,
            tts_chars_filtered,
            tts_emotion_cues_added,
            auto_tags_inferred,
            sentence_tags,
            explicit_tags_applied,
            explicit_tags_seen,
            tts_emotion_cue_style,
            tts_text_hash,
            tts_preview,
        )
        rel_score = merged_context.get("relationship_score", req.user_context.relationship_score)
        emo_vector = affect_to_emo_vector(
            affect_vector=_tts_affect,
            emotion_label=emotional_state.current_emotion,
            emotion_intensity=emotional_state.intensity,
            relationship_score=rel_score,
        )
        tts_request_payload = {
            "request_id": req.request_id,
            "text": clipped_speech,
            "relationship_score": rel_score,
            "emotion": emotional_state.current_emotion,
            "emotion_intensity": emotional_state.intensity,
            "emotion_variant": emotion_hints.suggested_variant,
            "emo_vector": emo_vector,
        }
        if req.defer_tts:
            logger.info("[%s] TTS deferred: returning speech job to client", req.request_id)
        else:
            indextts_url = settings["services"]["tts"].get("adapter_url", "")
            tts_url = settings["services"]["tts"]["url"]

            # Singing branch: text intent detection disabled — use /sing slash command instead
            singing_enabled = False
            singing_intent: str | None = None
            if singing_enabled and not req.game_event_context and not req.proactive:
                singing_intent = detect_singing_intent(req.message)

            if singing_intent:
                logger.info("[%s] Singing intent detected: '%s'", req.request_id, singing_intent)
                singing_audio = await run_singing_pipeline(
                    song=singing_intent,
                    request_id=req.request_id,
                    settings=settings,
                )
                if singing_audio:
                    tts_audio = singing_audio
                    timing.mark("tts_first_chunk")
                    logger.info("[%s] Singing pipeline OK — %d bytes", req.request_id, len(tts_audio))
                else:
                    logger.warning("[%s] Singing pipeline failed, falling back to normal TTS", req.request_id)

            # Sentence-streaming: if segments were synthesized during Brain streaming and
            # the text survived post-processing, assemble them (with text-derived pauses)
            # instead of the one-call synthesis.
            if _stream_speech is not None and tts_audio is None:
                _ss_audio, _ss_stats = await _stream_speech.finalize(response_text)
                if _ss_audio is not None:
                    tts_audio = _ss_audio
                    timing.mark("tts_first_chunk")
                    logger.info(
                        "[%s] Sentence-stream TTS OK: %d/%d sentences, %d bytes",
                        req.request_id, _ss_stats.synthesized, _ss_stats.sentences,
                        len(tts_audio),
                    )
                else:
                    logger.info(
                        "[%s] Sentence-stream fell back to one-call TTS (%s)",
                        req.request_id, _ss_stats.reason,
                    )

            if tts_audio is None:
                try:
                    # 45 s: healthy synthesis of a clipped reply is ≤10 s; anything past
                    # 45 s is a wedged adapter (VRAM spill) and must fail fast rather
                    # than hold the whole chat response hostage (was 120 s).
                    async with httpx.AsyncClient(timeout=45.0) as tts_client:
                        if indextts_url:
                            try:
                                tts_resp = await tts_client.post(
                                    f"{indextts_url}/synthesize",
                                    json=tts_request_payload,
                                )
                                tts_resp.raise_for_status()
                                tts_audio = base64.b64decode(tts_resp.json()["wav_base64"])
                                timing.mark("tts_first_chunk")
                                logger.info("[%s] IndexTTS synthesis OK — emo_vector=%s",
                                            req.request_id, [round(v, 3) for v in emo_vector] if emo_vector else None)
                            except httpx.HTTPError as exc:
                                logger.warning("[%s] IndexTTS unavailable (%s), falling back to QwenTTS", req.request_id, exc)
                                tts_resp = await tts_client.post(
                                    f"{tts_url}/v1/synthesize",
                                    json=tts_request_payload,
                                )
                                tts_resp.raise_for_status()
                                tts_audio = tts_resp.content
                                timing.mark("tts_first_chunk")
                        else:
                            tts_resp = await tts_client.post(
                                f"{tts_url}/v1/synthesize",
                                json=tts_request_payload,
                            )
                            tts_resp.raise_for_status()
                            tts_audio = tts_resp.content
                            timing.mark("tts_first_chunk")
                except httpx.HTTPError as exc:
                    logger.warning("[%s] TTS synthesis error: %s", req.request_id, exc)

    timings = timing.finalize()
    
    # --- Save audio to temp file if TTS succeeded ---
    audio_path: str | None = None
    if tts_audio:
        try:
            from pathlib import Path
            import tempfile
            # Save to temp directory with request ID in filename for debugging
            temp_dir = Path(tempfile.gettempdir()) / "koroki_audio"
            temp_dir.mkdir(exist_ok=True)
            audio_path = str(temp_dir / f"{req.request_id}.wav")
            with open(audio_path, "wb") as f:
                f.write(tts_audio)
            logger.info("[%s] Saved audio to: %s (%d bytes)", req.request_id, audio_path, len(tts_audio))
        except Exception as exc:
            logger.error("[%s] Failed to save audio file: %s", req.request_id, exc)
            audio_path = None
    
    # Progress relationship score gradually to avoid overnight rank jumps.
    # Proactive reach-outs do not count as user messages for scoring.
    if not merged_context.get("is_owner", False) and not req.proactive:
        counter = max(0, int(cached_user_payload.get("relationship_message_counter", 0))) + 1
        increments = counter // relationship_step_messages
        counter = counter % relationship_step_messages
        if increments > 0:
            _old_score = int(merged_context.get("relationship_score", 0))
            _new_score = min(100, _old_score + increments)
            merged_context["relationship_score"] = _new_score
            # Detect tier crossing — store as pending so the NEXT response reflects the shift naturally.
            def _rel_tier(s: int) -> str:
                if s >= 80: return "devoted"
                if s >= 60: return "cherished"
                if s >= 40: return "trusted"
                if s >= 20: return "acquainted"
                return "stranger"
            if _rel_tier(_old_score) != _rel_tier(_new_score):
                merged_context["pending_milestone"] = {
                    "from_tier": _rel_tier(_old_score),
                    "to_tier": _rel_tier(_new_score),
                }
        merged_context["relationship_message_counter"] = counter

    # Append the current exchange to recent_turns.
    # Proactive messages have no preceding user turn — store only the assistant side,
    # and never store a chosen [silent]: the bot doesn't send it, so persisting it
    # would stack phantom unanswered turns and trip the double-text cap for nothing.
    # Game commentary (game_event_context set) is a one-shot directive — never persist
    # as a conversation turn; doing so contaminates the user's history with chess lines.
    _chose_silence = req.proactive and "[silent]" in response_text.strip().lower()
    if req.game_event_context and not req.proactive:
        # Keep existing turns unchanged so game commentary doesn't bleed into conversation.
        merged_context["recent_turns"] = list(cached_user_payload.get("recent_turns") or [])
    else:
        _turns = list(merged_context.get("recent_turns") or [])
        if not req.proactive:
            _turns.append({"role": "user", "content": _message_with_sight[:2000]})
        if not _chose_silence:
            _turns.append({"role": "assistant", "content": response_text})
        keep_turns = max(2, min(max_recent_turns, 8))
        merged_context["recent_turns"] = _turns[-keep_turns:]

    memory_payload = decay_memory_state(dict(cached_user_payload))
    memory_payload.update(
        {
            "relationship_score": merged_context.get("relationship_score", req.user_context.relationship_score),
            "is_owner": merged_context.get("is_owner", req.user_context.is_owner),
            "last_summary": merged_context.get("last_summary"),
            # Persist only durable user facts. Runtime-only emotion and RAG facts
            # should influence the current prompt, not permanently bloat memory.
            "core_facts": persistent_core_facts[:max_core_facts],
            "recent_turns": merged_context.get("recent_turns"),
            "known_users": known_users,
            "relationship_message_counter": merged_context.get(
                "relationship_message_counter",
                cached_user_payload.get("relationship_message_counter", 0),
            ),
            "emotional_state": emotional_state.to_dict(),
        }
    )
    # Persist pending_milestone (set this request) or cleared value (injected this request).
    memory_payload["pending_milestone"] = merged_context.get("pending_milestone")
    # Track real user contact time for social rhythm (absence-based affect drift in scheduler).
    if not req.proactive:
        memory_payload["last_contact_at_ts"] = int(time.time())
    # Persist the dominant emotion of this session for carry-forward on the next opener.
    memory_payload["last_session_emotion"] = emotional_state.current_emotion
    # Game commentary is a one-shot — skip episodic consolidation entirely so chess
    # moves never appear in the user's memory as conversation episodes.
    if not req.game_event_context:
        consolidation_result = consolidate_user_memory(
            memory_payload,
            user_message=_message_with_sight,
            response_text=response_text,
            emotional_state=emotional_state.to_dict(),
            cognitive_snapshot=reflective_snapshot,
        )
    else:
        consolidation_result = None
    await user_memory_cache.upsert(req.user_context.user_id, memory_payload, dirty=True)
    flushed_records = await user_memory_cache.flush_dirty()

    # Rumination: add this exchange to background queue + consume any surfaced item.
    if not req.proactive and not req.game_event_context and response_text:
        _rel = int(merged_context.get("relationship_score", req.user_context.relationship_score))
        _intensity = float(getattr(emotional_state, "intensity", 0.5) or 0.5)
        _coherence = float(getattr(reflective_snapshot, "coherence_score", 0.5) or 0.5) if reflective_snapshot else 0.5
        # Resonance: high relationship, emotional, coherent conversations resonate more.
        _resonance = min(1.0, (_rel / 100.0) * 0.4 + _intensity * 0.35 + _coherence * 0.25)
        if _resonance >= 0.15:
            _summary = req.message[:120].strip()
            add_rumination(_summary, _resonance, req.user_context.user_id)
        # Consume surfaced rumination now that it has been injected into this prompt.
        consume_surfaced_rumination()

    logger.info(
        "[%s] Memory save: owner=%s relationship_score=%s rel_counter=%s/%s recent_turns=%d flushed=%d",
        req.request_id,
        merged_context.get("is_owner", False),
        merged_context.get("relationship_score", req.user_context.relationship_score),
        merged_context.get("relationship_message_counter", cached_user_payload.get("relationship_message_counter", 0)),
        relationship_step_messages,
        len(merged_context.get("recent_turns") or []),
        flushed_records,
    )

    return {
        "request_id": req.request_id,
        "text": response_text,
        "adapter_used": adapter_used,
        "audio_path": audio_path,
        "has_audio": tts_audio is not None,
        "tts_deferred": bool(req.defer_tts and tts_request_payload),
        "tts_request": tts_request_payload if req.defer_tts else None,
        "audio_length_bytes": len(tts_audio) if tts_audio else 0,
        "next_response_buffer_ready": bool(slot.next_response),
        "next_response_enabled": bool(slot.next_response and slot.next_response.enabled),
        "timings": timings,
        "evaluation": {
            "surface": "web" if is_web_platform else "discord",
            "discord_contract_target": not is_web_platform,
            "cognition_enabled": cognition_enabled,
            "cognition_runtime_context_enabled": cognition_runtime_context_enabled,
            "cognition_coherence_score": (
                reflective_snapshot.coherence_score if reflective_snapshot else None
            ),
            "cognition_affect_stability": (
                reflective_snapshot.affect_stability if reflective_snapshot else None
            ),
            "cognition_memory_coherence": (
                reflective_snapshot.memory_coherence if reflective_snapshot else None
            ),
            "cognition_intent_strength": (
                reflective_snapshot.intent_strength if reflective_snapshot else None
            ),
            "cognition_attention_entropy": (
                reflective_snapshot.attention_entropy if reflective_snapshot else None
            ),
            "cognition_initiative_drive": (
                reflective_snapshot.initiative_drive if reflective_snapshot else None
            ),
            "cognition_reflection_score": (
                reflective_snapshot.reflection_score if reflective_snapshot else None
            ),
            "cognition_proactive_eligible": (
                reflective_snapshot.proactive_eligible if reflective_snapshot else None
            ),
            "cognition_proactive_cooldown_s": (
                reflective_snapshot.proactive_cooldown_s if reflective_snapshot else None
            ),
            "memory_consolidation": {
                "episode_id": consolidation_result.episode.get("id") if consolidation_result else None,
                "salience": consolidation_result.episode.get("salience") if consolidation_result else None,
                "topics": consolidation_result.episode.get("topics") if consolidation_result else None,
                "memory_summary": consolidation_result.memory_summary if consolidation_result else None,
            },
            "think_filter_removed": think_removed,
            "repetition_trim_enabled": repetition_tail_trim_enabled,
            "repetition_trimmed": repetition_trimmed,
            "repetition_trim_ratio": repetition_ratio,
            "guillotine_triggered": False,
            "tts_shaping_requested": tts_shaping_requested,
            "tts_action_stripping_enabled": tts_action_stripping_enabled,
            "tts_action_chars_filtered": tts_chars_filtered,
            "tts_emotion_cues_enabled": tts_emotion_cues_enabled,
            "tts_emotion_cues_added": tts_emotion_cues_added,
            "tts_auto_emotion_tags_enabled": tts_auto_emotion_tags_enabled,
            "tts_auto_tags_inferred": auto_tags_inferred,
            "tts_explicit_emotion_tags_enabled": tts_explicit_emotion_tags_enabled,
            "tts_explicit_tags_applied": explicit_tags_applied,
            "tts_sentence_emotion_cues_enabled": tts_sentence_emotion_cues_enabled,
            "tts_sentence_tags": sentence_tags,
            "tts_sanitize_speech_enabled": tts_sanitize_speech_enabled,
            "tts_pacing_shaping_enabled": tts_pacing_shaping_enabled,
            "tts_period_pause_softening_enabled": tts_period_pause_softening_enabled,
            "tts_request_generated": bool(tts_request_payload),
            # Emotion feedback loop diagnostics
            "expressed_emotion": _expressed_emotion,
            "emotion_diverged": _emotion_diverged,
            "reflection_issues": _reflection_issues or [],
            "thinking_enabled": _thinking_enabled,
            # Emotion state snapshot
            "current_emotion": emotional_state.current_emotion,
            "emotion_intensity": emotional_state.intensity,
            "relationship_score": merged_context.get(
                "relationship_score", req.user_context.relationship_score
            ),
            "affect_vector": dict(emotional_state.affect_vector) if emotional_state.affect_vector else {},
        },
    }
