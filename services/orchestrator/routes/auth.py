from __future__ import annotations

import base64
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..auth import auth_store
from ..memory.cache import user_memory_cache
from shared.utils.config import get_settings

router = APIRouter()

SESSION_COOKIE_NAME = "koroki_web_session"
AUTH_VOICE_CUES: dict[str, dict[str, str | int]] = {
    "login_success": {
        "text": "Welcome back. Try not to keep me waiting again.",
        "emotion": "caring",
        "intensity": 62,
        "variant": "caring_tender",
    },
    "signup_success": {
        "text": "Your place in my court is ready now. Behave yourself.",
        "emotion": "playful",
        "intensity": 66,
        "variant": "playful_regal",
    },
    "logout": {
        "text": "Leaving already? Very well. I will remember.",
        "emotion": "thoughtful",
        "intensity": 48,
        "variant": "thoughtful_soft",
    },
    "wrong_password": {
        "text": "No. That password is wrong. Try again properly.",
        "emotion": "firm",
        "intensity": 58,
        "variant": "firm_regal",
    },
    "missing_username": {
        "text": "That username does not exist in my court.",
        "emotion": "cold",
        "intensity": 44,
        "variant": "cold_regal",
    },
    "duplicate_email": {
        "text": "That email already belongs to an account. Do keep up.",
        "emotion": "playful",
        "intensity": 60,
        "variant": "playful_regal",
    },
    "duplicate_username": {
        "text": "That username is already taken. Choose another one.",
        "emotion": "annoyed",
        "intensity": 46,
        "variant": "annoyed_sharp",
    },
}


class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    email: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)


class AuthVoiceRequest(BaseModel):
    cue: str = Field(min_length=1, max_length=64)


async def _build_session_payload(user: dict) -> dict:
    memory_payload = await user_memory_cache.get(user["user_id"])
    relationship_score = int(memory_payload.get("relationship_score", 60) or 60)
    is_owner = bool(memory_payload.get("is_owner", False))
    return {
        "authenticated": True,
        "user": {
            "user_id": user["user_id"],
            "username": user["username"],
            "email": user.get("email", ""),
            "relationship_score": relationship_score,
            "is_owner": is_owner,
            "created_at": user["created_at"],
        },
    }


async def synthesize_voice_line(
    *, text: str, emotion: str, intensity: int, variant: str,
    prefix: str = "cue", relationship_score: int = 60,
) -> dict:
    """Synthesize one fixed in-character line via the TTS service and return its served URL.

    Shared by auth cues and the cinematic world cues (routes/world.py) — one production
    path for "Koroki speaks on a UI event", no parallel implementations.
    """
    settings = get_settings()
    repo_root = Path(__file__).resolve().parents[3]
    assets_root = repo_root / "assets" / "generated" / "audio"
    assets_root.mkdir(parents=True, exist_ok=True)

    output_name = f"{prefix}_{uuid.uuid4().hex[:8]}.wav"
    output_path = assets_root / output_name

    payload = {
        "request_id": f"{prefix}_{uuid.uuid4().hex[:8]}",
        "text": text,
        "relationship_score": relationship_score,
        "emotion": emotion,
        "emotion_intensity": intensity,
        "emotion_variant": variant,
    }
    # Same engine preference as the chat path: IndexTTS adapter when configured
    # (JSON wav_base64 at /synthesize), legacy QwenTTS otherwise (raw wav at /v1/synthesize).
    # Pre-fix, this always hit the dead legacy port — auth/world voice cues were silently broken.
    adapter_url = settings["services"]["tts"].get("adapter_url", "")
    async with httpx.AsyncClient(timeout=120.0) as client:
        if adapter_url:
            response = await client.post(f"{adapter_url}/synthesize", json=payload)
            response.raise_for_status()
            output_path.write_bytes(base64.b64decode(response.json()["wav_base64"]))
        else:
            response = await client.post(
                f"{settings['services']['tts']['url']}/v1/synthesize", json=payload,
            )
            response.raise_for_status()
            output_path.write_bytes(response.content)

    return {"audio_url": f"/assets/generated/audio/{output_name}", "text": text}


async def _synthesize_auth_voice(cue: str) -> dict:
    payload = AUTH_VOICE_CUES.get(cue)
    if not payload:
        raise HTTPException(status_code=404, detail="Unknown auth voice cue.")
    return await synthesize_voice_line(
        text=payload["text"], emotion=payload["emotion"],
        intensity=payload["intensity"], variant=payload["variant"], prefix=f"auth_{cue}",
    )


@router.get("/auth/session")
async def get_session(request: Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user = auth_store.get_session_user(token)
    if not user:
        return {"authenticated": False, "user": None}
    return await _build_session_payload(user)


@router.post("/auth/signup")
async def signup(req: SignupRequest, response: Response) -> dict:
    try:
        user = auth_store.register_user(req.username, req.password, req.email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await user_memory_cache.upsert(
        user["user_id"],
        {
            "relationship_score": 60,
            "is_owner": False,
            "recent_turns": [],
            "core_facts": [],
            "known_users": [],
        },
        dirty=True,
    )
    await user_memory_cache.flush_dirty()

    token = auth_store.create_session(user["user_id"])
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 24 * 30,
        path="/",
    )
    return await _build_session_payload(user)


@router.post("/auth/login")
async def login(req: LoginRequest, response: Response) -> dict:
    user, error_message = auth_store.authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail=error_message or "Login failed.")

    token = auth_store.create_session(user["user_id"])
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 24 * 30,
        path="/",
    )
    return await _build_session_payload(user)


@router.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    auth_store.delete_session(token)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/auth/voice")
async def auth_voice(req: AuthVoiceRequest) -> dict:
    try:
        return await _synthesize_auth_voice(req.cue)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Auth voice synthesis failed: {exc}") from exc
