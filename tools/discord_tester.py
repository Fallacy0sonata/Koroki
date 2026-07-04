"""
Claude tester — sends/reads Discord messages as a second bot account.

Requires TESTER_BOT_TOKEN in .env (separate bot from Koroki).
Steps to set up:
  1. Go to discord.com/developers/applications → New Application
  2. Bot tab → Add Bot → copy token → paste into .env as TESTER_BOT_TOKEN
  3. OAuth2 → URL Generator → bot scope + Send Messages + Read Message History
  4. Invite the bot to guild 1503131422553018408

Usage (from Koroki root, main .venv active):
  python tools/discord_tester.py channels
  python tools/discord_tester.py send <channel_id> <message text>
  python tools/discord_tester.py send-image <channel_id> <image_path> [message text]
  python tools/discord_tester.py read <channel_id> [limit]
  python tools/discord_tester.py tail                     # tail guild_activity.jsonl

Examples:
  python tools/discord_tester.py send 1503131559019151410 "@Koroki hey"
  python tools/discord_tester.py read 1503131559019151410 20
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

TOKEN = os.getenv("TESTER_BOT_TOKEN", "").strip()
GUILD_ID = 1503131422553018408
API = "https://discord.com/api/v10"
_ACTIVITY_LOG = BASE_DIR / "data" / "logs" / "guild_activity.jsonl"


def _headers() -> dict:
    if not TOKEN:
        print("ERROR: TESTER_BOT_TOKEN not set in .env", file=sys.stderr)
        print("Create a second Discord bot, invite it to the test guild, set the token.", file=sys.stderr)
        sys.exit(1)
    return {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}


async def cmd_channels() -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{API}/guilds/{GUILD_ID}/channels", headers=_headers())
        resp.raise_for_status()
        channels = resp.json()
    text_channels = [c for c in channels if c.get("type") == 0]
    for ch in sorted(text_channels, key=lambda c: c.get("position", 0)):
        print(f"  {ch['id']}  #{ch['name']}")


async def cmd_send(channel_id: int, content: str) -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API}/channels/{channel_id}/messages",
            headers=_headers(),
            json={"content": content},
        )
        if resp.status_code != 200:
            print(f"ERROR {resp.status_code}: {resp.text}", file=sys.stderr)
            sys.exit(1)
    msg = resp.json()
    ts = msg.get("timestamp", "")[:19]
    print(f"[{ts}] Sent ({msg['id']}): {msg['content'][:120]}")


async def cmd_send_image(channel_id: int, image_path: str, content: str = "") -> None:
    """Send an image attachment (multipart) — for testing her vision service."""
    path = Path(image_path)
    if not path.exists():
        print(f"ERROR: image not found: {path}", file=sys.stderr)
        sys.exit(1)
    headers = {k: v for k, v in _headers().items() if k != "Content-Type"}
    payload = {"content": content} if content else {}
    files = {
        "payload_json": (None, json.dumps(payload), "application/json"),
        "files[0]": (path.name, path.read_bytes(), "image/png"),
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{API}/channels/{channel_id}/messages", headers=headers, files=files
        )
        if resp.status_code != 200:
            print(f"ERROR {resp.status_code}: {resp.text}", file=sys.stderr)
            sys.exit(1)
    msg = resp.json()
    ts = msg.get("timestamp", "")[:19]
    n_att = len(msg.get("attachments") or [])
    print(f"[{ts}] Sent ({msg['id']}) with {n_att} attachment(s): {msg.get('content', '')[:120]}")


async def cmd_read(channel_id: int, limit: int = 15) -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API}/channels/{channel_id}/messages",
            headers=_headers(),
            params={"limit": min(limit, 50)},
        )
        resp.raise_for_status()
        messages = resp.json()
    for msg in reversed(messages):
        ts = msg.get("timestamp", "")[:19]
        author = msg["author"]["username"]
        content = (msg.get("content") or "").replace("\n", " ")
        bot_tag = " [bot]" if msg["author"].get("bot") else ""
        print(f"[{ts}] {author}{bot_tag}: {content[:200]}")


def cmd_tail(n: int = 30) -> None:
    """Print the last N lines from guild_activity.jsonl."""
    if not _ACTIVITY_LOG.exists():
        print("No guild_activity.jsonl yet — start the bot and send some messages.")
        return
    lines = _ACTIVITY_LOG.read_text(encoding="utf-8").splitlines()
    for line in lines[-n:]:
        try:
            entry = json.loads(line)
            ts = entry.get("ts", "")[:19]
            ch = entry.get("channel_name", "?")
            user = entry.get("username", "?")
            content = (entry.get("content") or "").replace("\n", " ")
            bot_tag = " [bot]" if entry.get("is_bot") else ""
            print(f"[{ts}] #{ch} {user}{bot_tag}: {content[:200]}")
        except Exception:
            print(line)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]
    if cmd == "channels":
        asyncio.run(cmd_channels())
    elif cmd == "send":
        if len(args) < 3:
            print("Usage: discord_tester.py send <channel_id> <message>", file=sys.stderr)
            sys.exit(1)
        asyncio.run(cmd_send(int(args[1]), " ".join(args[2:])))
    elif cmd == "send-image":
        if len(args) < 3:
            print("Usage: discord_tester.py send-image <channel_id> <image_path> [message]", file=sys.stderr)
            sys.exit(1)
        text = " ".join(args[3:]) if len(args) > 3 else ""
        asyncio.run(cmd_send_image(int(args[1]), args[2], text))
    elif cmd == "read":
        if len(args) < 2:
            print("Usage: discord_tester.py read <channel_id> [limit]", file=sys.stderr)
            sys.exit(1)
        limit = int(args[2]) if len(args) > 2 else 15
        asyncio.run(cmd_read(int(args[1]), limit))
    elif cmd == "tail":
        n = int(args[1]) if len(args) > 1 else 30
        cmd_tail(n)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
