"""Koroki Twitch chat surface — the first streamer-facing ingest.

Connects to Twitch IRC (TLS), reads the channel's chat, selects messages worth her
attention, and routes them through the same orchestrator pipeline as Discord — so
Twitch viewers meet the same mind (memory, hormones, guillotine, all of it).

Reading requires NO credentials: Twitch permits anonymous read-only connections with a
justinfan nick. Replying in chat requires TWITCH_TOKEN + TWITCH_NICK in .env; without
them the bot runs in listen-only mode (she still experiences chat — messages flow into
her pipeline — she just can't type back yet).

Message selection mirrors the Discord presence philosophy: she is not a reply-all bot.
  - direct address (her name) → always considered, per-user cooldown applies
  - everything else → sampled at a rate that scales INVERSELY with chat speed
    (busy chat = she dips in occasionally; slow chat = she's more present)
Selection state is plain and testable (pure functions, no sockets involved).

Run: .venv\\Scripts\\python.exe twitch_bot.py   (settings: streaming.twitch in settings.yaml)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import ssl
import time
from dataclasses import dataclass, field

import httpx

from shared.utils.config import get_settings

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("twitch_bot")

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697

_NAME_PAT = re.compile(r"\bkoroki\b", re.IGNORECASE)


# ── protocol parsing (pure, tested) ──────────────────────────────────────────

@dataclass(frozen=True)
class ChatMessage:
    login: str          # twitch login name
    text: str
    channel: str


def parse_irc_line(line: str) -> ChatMessage | str | None:
    """Parse one IRC line. Returns ChatMessage for PRIVMSG, 'PING' for pings,
    None for everything else."""
    line = line.strip()
    if not line:
        return None
    if line.startswith("PING"):
        return "PING"
    # [@tags ]:login!login@login.tmi.twitch.tv PRIVMSG #channel :text
    m = re.match(r"^(?:@\S+ )?:(\w+)!\S+ PRIVMSG #(\S+) :(.*)$", line)
    if m:
        return ChatMessage(login=m.group(1).lower(), text=m.group(3), channel=m.group(2))
    return None


# ── selection (pure, tested) ─────────────────────────────────────────────────

@dataclass
class SelectionState:
    last_reply_ts: float = 0.0
    per_user_last_ts: dict[str, float] = field(default_factory=dict)
    recent_msg_ts: list[float] = field(default_factory=list)


def chat_msgs_per_min(state: SelectionState, now: float, window_s: float = 60.0) -> float:
    state.recent_msg_ts = [t for t in state.recent_msg_ts if now - t <= window_s]
    return len(state.recent_msg_ts) / (window_s / 60.0)


def should_respond(
    state: SelectionState,
    msg: ChatMessage,
    now: float,
    *,
    global_cooldown_s: float = 20.0,
    user_cooldown_s: float = 60.0,
    rng: random.Random | None = None,
) -> bool:
    """Decide whether this message deserves her attention. Pure & deterministic
    given the rng. Callers must have already appended `now` to recent_msg_ts."""
    rng = rng or random
    if now - state.last_reply_ts < global_cooldown_s:
        return False
    if now - state.per_user_last_ts.get(msg.login, 0.0) < user_cooldown_s:
        return False
    if _NAME_PAT.search(msg.text):
        return True
    # ambient sampling: busier chat → lower per-message probability, floor at 2%
    rate = chat_msgs_per_min(state, now)
    p = max(0.02, min(0.35, 3.0 / max(rate, 1.0) * 0.1))
    return rng.random() < p


def mark_replied(state: SelectionState, msg: ChatMessage, now: float) -> None:
    state.last_reply_ts = now
    state.per_user_last_ts[msg.login] = now


# ── orchestrator bridge ──────────────────────────────────────────────────────

async def query_orchestrator(orch_url: str, msg: ChatMessage) -> str | None:
    payload = {
        "request_id": f"twitch_{msg.login}_{int(time.time() * 1000) % 10**9}",
        "message": msg.text,
        "user_context": {
            "user_id": f"twitch_{msg.login}",
            "relationship_score": 10,     # strangers start cold; memory grows them
            "is_owner": False,
            "mode": "auto",
            "platform": "twitch",
        },
        "defer_tts": True,                # v1 is text; stream audio routing comes later
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(f"{orch_url}/v1/chat", json=payload)
            r.raise_for_status()
            return (r.json().get("text") or "").strip() or None
    except Exception as exc:
        logger.warning("orchestrator query failed: %s", exc)
        return None


# ── IRC client loop ──────────────────────────────────────────────────────────

class TwitchChat:
    def __init__(self) -> None:
        settings = get_settings()
        cfg = settings.get("streaming", {}).get("twitch", {})
        self.channel = str(cfg.get("channel", "")).lstrip("#").lower()
        self.respond_enabled = bool(cfg.get("respond", False))
        self.orch_url = settings["services"]["orchestrator"]["url"]
        self.token = os.getenv("TWITCH_TOKEN", "")
        self.nick = os.getenv("TWITCH_NICK", "").lower()
        if not self.token or not self.nick:
            self.nick = f"justinfan{random.randint(10000, 99999)}"
            self.token = ""
            self.respond_enabled = False
            logger.info("no TWITCH_TOKEN — anonymous listen-only mode")
        self.state = SelectionState()
        self._writer: asyncio.StreamWriter | None = None

    async def run(self) -> None:
        if not self.channel:
            logger.error("streaming.twitch.channel not set in settings.yaml — exiting")
            return
        backoff = 2
        while True:
            try:
                await self._session()
                backoff = 2
            except Exception as exc:
                logger.warning("IRC session ended (%s) — reconnecting in %ds", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

    async def _session(self) -> None:
        ctx = ssl.create_default_context()
        reader, writer = await asyncio.open_connection(IRC_HOST, IRC_PORT, ssl=ctx)
        self._writer = writer
        if self.token:
            await self._send(f"PASS oauth:{self.token.removeprefix('oauth:')}")
        await self._send(f"NICK {self.nick}")
        await self._send(f"JOIN #{self.channel}")
        logger.info("joined #%s as %s", self.channel, self.nick)

        while True:
            raw = await reader.readline()
            if not raw:
                raise ConnectionError("connection closed")
            parsed = parse_irc_line(raw.decode("utf-8", errors="replace"))
            if parsed == "PING":
                await self._send("PONG :tmi.twitch.tv")
            elif isinstance(parsed, ChatMessage):
                await self._on_message(parsed)

    async def _send(self, line: str) -> None:
        assert self._writer is not None
        self._writer.write((line + "\r\n").encode("utf-8"))
        await self._writer.drain()

    async def _on_message(self, msg: ChatMessage) -> None:
        now = time.time()
        self.state.recent_msg_ts.append(now)
        if msg.login == self.nick:
            return
        if not should_respond(self.state, msg, now):
            return
        mark_replied(self.state, msg, now)
        logger.info("responding to %s: %r", msg.login, msg.text[:80])
        text = await query_orchestrator(self.orch_url, msg)
        if not text:
            return
        if self.respond_enabled and self.token:
            # Twitch caps messages at 500 chars
            await self._send(f"PRIVMSG #{self.channel} :{text[:480]}")
        else:
            logger.info("[listen-only] she would say: %r", text[:200])


if __name__ == "__main__":
    asyncio.run(TwitchChat().run())
