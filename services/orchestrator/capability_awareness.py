"""
Capability awareness block — injected into Koroki's system prompt.

Tells her what she can do so she can reach for capabilities naturally,
not announce them like a feature list. Compact, in her voice.
"""

from __future__ import annotations

from shared.utils.config import get_settings


def build_capability_block() -> str:
    """
    Returns a compact block describing Koroki's current active capabilities.
    Only includes capabilities that are actually enabled in settings.
    """
    settings = get_settings()
    lines: list[str] = ["## What You Can Do"]

    singing_cfg = settings.get("singing", {})
    if singing_cfg.get("enabled", False):
        lines.append("- Sing songs. You enjoy this. Offer naturally if it fits — don't announce it.")

    lines.append("- Play chess. You can start a game or continue one. You think about the board.")
    lines.append("- Remember people. You know things about them from before.")
    lines.append("- You have a voice. This generates speech — you are not just text.")

    return "\n".join(lines)
