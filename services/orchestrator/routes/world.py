"""
World-state aggregation endpoint.

`GET /v1/worldstate` returns a single snapshot of Koroki's body + room + presence + felt
state, aggregated from the subsystems that already run continuously. This is the
prerequisite for the frontend "window into her world" vision (multi-room rendering reads
this): the backend already simulates weather/ambient/lighting/sleep/energy/endocrine, but
nothing exposed it all in one place (only `/v1/nervstate` existed).

Two views in the response:
- `felt` — natural-language descriptors (what the captain LLM reads / the frontend captions).
- everything else (`room`, `body`, `presence`, numeric fields) — raw values for the FRONTEND
  RENDERER to draw with. **These raw numbers are NOT for the LLM** (CLAUDE.md: the captain reads
  sensations, not numbers).

Read-only and defensive: each subsystem is wrapped so one failing getter degrades that
section to null instead of 500-ing the whole endpoint.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from .auth import synthesize_voice_line

router = APIRouter()
logger = logging.getLogger("orchestrator.world")

# Fixed in-character lines Koroki speaks on cinematic-world UI events (entering, changing
# rooms). Same mechanism as the auth voice cues — synthesized live via IndexTTS. Kept short
# and cool/tsundere-warm to match her voice. (Future: route through the brain for mood-aware
# lines instead of fixed text.)
WORLD_VOICE_CUES: dict[str, dict] = {
    "enter": {"text": "Oh — you actually came in. ...Fine. Don't just stand at the door.",
              "emotion": "playful", "intensity": 56, "variant": "playful_regal"},
    "room_studio": {"text": "My stream corner. This is where the noise happens.",
                    "emotion": "playful", "intensity": 52, "variant": "firm_regal"},
    "room_bedroom": {"text": "...This is my room. Don't read into it.",
                     "emotion": "annoyed", "intensity": 50, "variant": "annoyed_sharp"},
    "room_lounge": {"text": "The lounge. I watch the city from here when it's quiet.",
                    "emotion": "firm", "intensity": 40, "variant": "firm_regal"},
}


def _safe(label: str, fn) -> Any:
    """Call a subsystem getter; on any failure log and return None (never 500)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — defensive aggregation boundary
        logger.debug("worldstate: %s section unavailable: %s", label, exc)
        return None


def _time_section() -> dict[str, Any]:
    from ..world.clock import hour_of_day, is_late_night, now, time_of_day_label

    n = now()
    return {
        "iso": n.isoformat(),
        "label": time_of_day_label(n),
        "hour": round(hour_of_day(n), 2),
        "is_late_night": is_late_night(n),
    }


def _presence_section() -> dict[str, Any]:
    from ..body.sleep import get_sleep

    s = get_sleep()
    return {
        "sleep_state": str(getattr(s.current_state(), "value", s.current_state())),
        "awake": bool(s.is_awake()),
        "asleep": bool(s.is_asleep()),
        "sleep_debt_hours": round(float(s.sleep_debt_hours()), 2),
    }


def _room_section() -> dict[str, Any]:
    from ..world.room.ambient import get_ambient
    from ..world.room.identity import get_canonical
    from ..world.room.lighting import get_lighting
    from ..world.room.weather import get_weather

    weather = _safe("weather", lambda: get_weather().current_state())
    ambient = _safe("ambient", get_ambient)
    lighting = _safe("lighting", lambda: round(float(get_lighting().level()), 3))
    identity = _safe("identity", get_canonical)
    return {
        "weather": weather,
        "temperature_c": round(float(ambient.temperature_c()), 1) if ambient else None,
        "humidity": round(float(ambient.humidity()), 2) if ambient else None,
        "lighting": lighting,
        "identity": identity,
    }


def _body_section() -> dict[str, Any]:
    from ..body.endocrine import get_endocrine
    from ..body.energy import get_energy

    energy = _safe("energy", lambda: round(float(get_energy().level()), 3))
    endocrine = _safe("endocrine", lambda: get_endocrine().snapshot())
    return {"energy": energy, "endocrine": endocrine}


def _felt_section() -> dict[str, Any]:
    from ..body.interoception import get_felt_state

    fs = get_felt_state()
    return {
        "block": fs.to_prompt_block(),
        "body": getattr(fs, "body", None),
        "mind": getattr(fs, "mind", None),
        "mood": getattr(fs, "mood", None),
        "context": getattr(fs, "context", None),
    }


def _nervous_section() -> dict[str, Any]:
    from ..nervous_system import build_state_block, get_state

    state = get_state()
    return {"state": state, "block": build_state_block(state)}


def _activity_section() -> dict[str, Any]:
    from ..mind.activities import get_activities
    from ..mind.journal import journal
    from ..mind.projects import get_projects

    return {
        "current": get_activities().current(),
        "today": journal().today_line(),
        "projects": get_projects().snapshot(),
    }


def _events_section() -> dict[str, Any]:
    from ..world.events import get_world_events

    return {"recent": get_world_events().recent(5)}


@router.get("/world/voice")
async def world_voice(cue: str) -> dict:
    """Synthesize Koroki's spoken line for a world UI event (enter / room_*).
    Returns {audio_url, text}. The frontend plays the audio and shows the line."""
    payload = WORLD_VOICE_CUES.get(cue)
    if not payload:
        raise HTTPException(status_code=404, detail="Unknown world voice cue.")
    return await synthesize_voice_line(
        text=payload["text"], emotion=payload["emotion"],
        intensity=payload["intensity"], variant=payload["variant"], prefix=f"world_{cue}",
    )


@router.get("/worldstate")
async def worldstate() -> dict:
    """Aggregated snapshot of Koroki's body/room/presence/felt state for the frontend."""
    return {
        "time": _safe("time", _time_section),
        "presence": _safe("presence", _presence_section),
        "room": _safe("room", _room_section),
        "body": _safe("body", _body_section),
        "felt": _safe("felt", _felt_section),
        "nervous": _safe("nervous", _nervous_section),
        "activity": _safe("activity", _activity_section),
        "events": _safe("events", _events_section),
    }
