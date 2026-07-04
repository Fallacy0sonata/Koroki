from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Platform(str, Enum):
    discord = "discord"
    desktop = "desktop"
    web = "web"
    minecraft = "minecraft"


class Mode(str, Enum):
    casual = "casual"
    roleplay = "roleplay"
    auto = "auto"
    owner = "owner"


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=2000)


class UserContext(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)
    relationship_score: Annotated[int, Field(ge=0, le=100)]
    is_owner: bool
    mode: Mode = Mode.auto
    platform: Platform = Platform.discord
    mentioned_user_ids: Optional[list[str]] = Field(default=None)
    last_summary: Optional[str] = Field(None, max_length=1200)
    core_facts: Optional[list[str]] = Field(default=None)
    recent_turns: Optional[list[Turn]] = Field(default=None)

    @field_validator("core_facts")
    @classmethod
    def limit_core_facts(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and len(v) > 20:
            raise ValueError("core_facts exceeds maximum of 20 items")
        return v

    @field_validator("recent_turns")
    @classmethod
    def limit_recent_turns(cls, v: list[Turn] | None) -> list[Turn] | None:
        if v is not None and len(v) > 8:
            raise ValueError("recent_turns exceeds maximum of 8 items")
        return v

    @field_validator("mentioned_user_ids")
    @classmethod
    def limit_mentioned_users(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and len(v) > 10:
            raise ValueError("mentioned_user_ids exceeds maximum of 10 items")
        return v


class ChatRequest(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=64)
    message: str = Field(..., min_length=1, max_length=2000)
    user_context: UserContext
    defer_tts: bool = False
    proactive: bool = False
    # Game event context — injected as first core_fact so the brain generates
    # in-character streamer commentary instead of a normal conversation reply.
    game_event_context: Optional[str] = Field(default=None, max_length=512)
    # Image attachments (base64) — described by the vision service into a
    # visual percept before the brain reads the message.
    images_b64: Optional[list[str]] = Field(default=None)

    @field_validator("images_b64")
    @classmethod
    def limit_images(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) > 2:
            raise ValueError("images_b64 exceeds maximum of 2 items")
        for s in v:
            if not s or len(s) > 12_000_000:
                raise ValueError("image exceeds maximum encoded size (~9 MB)")
        return v


class DeferredTTSRequest(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=128)
    text: str = Field(..., min_length=1, max_length=4000)
    relationship_score: Annotated[int, Field(ge=0, le=100)]
    emotion: str = Field(default="neutral", min_length=1, max_length=64)
    emotion_intensity: Annotated[int, Field(ge=0, le=100)] = 50
    emotion_variant: Optional[str] = Field(default=None, max_length=64)


class DeferredTTSResponse(BaseModel):
    request_id: str
    audio_url: str
    audio_path: str
    content_type: Literal["audio/wav"] = "audio/wav"


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    service: str
    version: str
    uptime_seconds: float


class ReadyResponse(BaseModel):
    ready: bool
    checks: dict[str, bool]


class VersionResponse(BaseModel):
    service: str
    version: str
    build: str = "2.0.0"
