from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NextResponseBuffer:
    """Buffer-ready slot for speculative or pre-generated next responses."""

    cache_key: str
    enabled: bool = False
    text: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def store(self, text: str, metadata: Optional[dict[str, str]] = None) -> None:
        self.text = text
        if metadata:
            self.metadata.update(metadata)

    def consume(self) -> str:
        value = self.text
        self.text = ""
        self.metadata.clear()
        return value


@dataclass
class InferenceSlot:
    request_id: str
    user_id: str
    primary_text: str = ""
    next_response: NextResponseBuffer | None = None

    def append_text(self, chunk: str) -> None:
        self.primary_text += chunk


def build_slot_cache_key(user_id: str, mode: str, relationship_score: int) -> str:
    return f"{user_id}:{mode}:{relationship_score}"


def create_inference_slot(
    request_id: str,
    user_id: str,
    mode: str,
    relationship_score: int,
    pre_generation_enabled: bool,
) -> InferenceSlot:
    cache_key = build_slot_cache_key(user_id, mode, relationship_score)
    next_response = NextResponseBuffer(cache_key=cache_key, enabled=pre_generation_enabled)
    return InferenceSlot(
        request_id=request_id,
        user_id=user_id,
        next_response=next_response,
    )
