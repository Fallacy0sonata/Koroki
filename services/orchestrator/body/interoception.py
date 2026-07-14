"""
Interoception — body state → natural language for the LLM.

Per the design docs captain-in-cabin philosophy: the LLM (captain) never sees raw
numbers from any subsystem. It reads a "felt-state snapshot" — natural
language describing what Koroki currently experiences.

This module:
  1. Gets a body-state snapshot from the endocrine engine
  2. Composes natural-language fragments contributed by each component
  3. Adds context from world clock (time of day, late night, etc.)
  4. Produces a small structured snapshot that prompt_builder can inject

Key insight (from atlas §10): the snapshot replaces "you should feel X" with
"you currently feel X." The LLM responds OUT OF the state instead of being
told what mood to express. Past events leave residue in the body; current
chemistry shapes voice.

Example output:
    FeltState(
        body="warmth in your chest, a low hum of tension under everything",
        mind="quiet engagement, things landing",
        mood="openhearted, mildly fond, a bit guarded",
        context="late night, you've been here a while"
    )

The context line is composed from world clock + (later phases) room state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .endocrine import get_endocrine
from .energy import get_energy
from .mood_compositions import compose_mood_fragments
from .sleep import get_sleep, SleepState
from ..world.clock import is_late_night, time_of_day_label
# Phase 2D — room subsystems. All four contribute fragments to felt-state.
from ..world.room.lighting import get_lighting
from ..world.room.ambient import get_ambient
from ..world.room.weather import get_weather

logger = logging.getLogger("orchestrator.body.interoception")


@dataclass
class FeltState:
    """The natural-language snapshot the LLM reads.

    Each field is a short phrase (or empty string if nothing notable).
    Never expose raw numbers — those go in `_raw` for debug only.
    """

    body: str = ""
    mind: str = ""
    mood: str = ""
    context: str = ""
    _raw: dict[str, Any] = None  # for debugging only — never injected into prompt

    def to_prompt_block(self) -> str:
        """Format as an injectable block for the system prompt.

        Returns empty string if there's nothing notable to inject (all neutral).
        Otherwise returns a small block like:
            "Felt state:
             - body: ...
             - mind: ...
             - mood: ...
             - context: ..."
        """
        lines = []
        if self.body:
            lines.append(f"  - body: {self.body}")
        if self.mind:
            lines.append(f"  - mind: {self.mind}")
        if self.mood:
            lines.append(f"  - mood: {self.mood}")
        if self.context:
            lines.append(f"  - context: {self.context}")
        if not lines:
            return ""
        return "Felt state right now:\n" + "\n".join(lines)


def _join_fragments(fragments: list[str], max_count: int = 3) -> str:
    """Join the most salient N fragments into a single phrase.

    Components contribute multiple fragments at different thresholds. We pick
    the first N (which are roughly ordered by intensity in each component's
    contribute_to_felt_state).
    """
    if not fragments:
        return ""
    selected = fragments[:max_count]
    if len(selected) == 1:
        return selected[0]
    if len(selected) == 2:
        return f"{selected[0]}, {selected[1]}"
    return f"{', '.join(selected[:-1])}, {selected[-1]}"


def _build_context_line(extra_context: list[str] | None = None) -> str:
    """Compose the context field from world clock + room state.

    Phase 1 — time-of-day. Phase 2D adds room context fragments (lighting,
    weather, ambient) passed in via extra_context.
    """
    parts = []
    tod = time_of_day_label()
    parts.append(tod)
    if is_late_night():
        parts.append("everything is quieter at this hour")
    if extra_context:
        parts.extend(extra_context)
    return ". ".join(parts)


def get_felt_state() -> FeltState:
    """Produce a fresh felt-state snapshot.

    Ticks the endocrine engine first (so decay/circadian/interactions are
    up to date), then:
      1. Collects per-component felt-state fragments (each component's view)
      2. Adds mood compositions — fear/thrill/excitement/safety emerge from
         hormone *combinations*, not single components. See mood_compositions.py.
      3. Assembles into natural-language snapshot.
    """
    engine = get_endocrine()
    # Ensure body state is current before reading. Tick sleep first because it
    # drives energy + can transition states, then tick endocrine so cortisol
    # baseline can reflect sleep debt.
    get_sleep().tick()
    engine.tick()
    snap = engine.snapshot()

    felt_fragments = snap.get("felt", {})
    per_component_body = list(felt_fragments.get("body", []))
    per_component_mind = list(felt_fragments.get("mind", []))
    per_component_mood = list(felt_fragments.get("mood", []))

    # Energy + sleep contribute their own felt-state fragments.
    energy_sleep_fragments: dict[str, list[str]] = {"body": [], "mind": [], "mood": []}
    get_energy().contribute_to_felt_state(energy_sleep_fragments)
    get_sleep().contribute_to_felt_state(energy_sleep_fragments)

    # Phase 2D — room subsystems tick and contribute context fragments.
    room_fragments: dict[str, list[str]] = {"body": [], "mind": [], "mood": [], "context": []}
    try:
        get_lighting().tick()
        get_ambient().tick()
        get_weather().tick()
        get_lighting().contribute_to_felt_state(room_fragments)
        get_ambient().contribute_to_felt_state(room_fragments)
        get_weather().contribute_to_felt_state(room_fragments)
    except Exception as _room_exc:
        logger.warning("Room subsystem tick failed (non-fatal): %s", _room_exc)

    # Compose emergent moods FIRST so they get priority over per-component fragments.
    # Per-research, emergent compositions (fear/thrill/safety) carry more semantic
    # meaning than individual hormone descriptions — the captain needs to read these
    # before the texture is truncated by max_count.
    # Pass *effective* levels (level × receptor sensitivity) so tolerance is reflected.
    composition_body: list[str] = []
    composition_mind: list[str] = []
    composition_mood: list[str] = []
    effective_levels = {name: comp.effective_level() for name, comp in engine.components.items()}
    compose_mood_fragments(
        effective_levels,
        {"body": composition_body, "mind": composition_mind, "mood": composition_mood},
    )

    # Priority order: sleep/energy state first (they're high-salience body state),
    # then compositions, then per-component nuance. Room ambient fragments go into
    # body/mood naturally if they were tagged there.
    body_list = energy_sleep_fragments["body"] + room_fragments["body"] + composition_body + per_component_body
    mind_list = energy_sleep_fragments["mind"] + composition_mind + per_component_mind
    mood_list = energy_sleep_fragments["mood"] + composition_mood + per_component_mood

    body_text = _join_fragments(body_list, max_count=2)
    mind_text = _join_fragments(mind_list, max_count=2)
    mood_text = _join_fragments(mood_list, max_count=3)
    # current activity joins the context line (lazy import — mind.activities reads body
    # modules, so importing at module level would be a cycle)
    context_extra = list(room_fragments["context"])
    try:
        from ..mind.activities import get_activities
        frag = get_activities().prompt_fragment()
        if frag:
            context_extra.append(frag)
    except Exception:
        pass
    # a fresh world event colors the moment ("just now, a thunderclap rattled the window")
    try:
        from ..world.events import get_world_events
        ev_frag = get_world_events().recent_fragment()
        if ev_frag:
            context_extra.append(ev_frag)
    except Exception:
        pass
    context_text = _build_context_line(extra_context=context_extra)

    state = FeltState(
        body=body_text,
        mind=mind_text,
        mood=mood_text,
        context=context_text,
        _raw=snap.get("raw", {}),
    )
    logger.debug(
        "FeltState: body=%r mind=%r mood=%r ctx=%r raw=%s",
        state.body, state.mind, state.mood, state.context, state._raw,
    )
    return state
