"""
Koroki 2.0 - Discord Bot Client
Phase 7: First Contact

A dumb terminal that routes Discord messages to the Orchestrator's /v1/chat endpoint.
No AI logic lives here—all personality, adaptation, and voice synthesis happens upstream.

The bot:
1. Detects the user's Discord ID
2. Flags is_owner=True if it matches OWNER_DISCORD_ID
3. Sends message + context to Orchestrator
4. Returns text response + .wav audio file as attachment

Owner Detection: 
  When OWNER_DISCORD_ID is matched, the bot injects:
    - is_owner=True
    - mode="auto" (Orchestrator selects appropriate speech synthesis)
    - relationship_score=100 (immaterial, but set for completeness)

Audio Handling (Day 1):
  - Orchestrator returns {text, audio_path}
  - Bot attaches the .wav file to the message
  - Voice channel streaming is Phase B
"""

import asyncio
import base64
import collections
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import random
import time
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
import httpx
from dotenv import load_dotenv

import stream_watch

# ────────────────────────────────────────────────────────────────────
# Logging Setup
# ────────────────────────────────────────────────────────────────────

_log_dir = Path(__file__).resolve().parent / "data" / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            str(_log_dir / "discord.log"),
            maxBytes=25 * 1024 * 1024,
            backupCount=4,
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("koroki.discord")

# Voice-handshake debugging: the 2026-07-04 vc_join timeout works standalone but
# stalls in-bot — DEBUG on the voice ws shows which op never arrives.
if os.getenv("KOROKI_VOICE_DEBUG", "false").strip().lower() == "true":
    for _vname in ("discord.voice_state", "discord.gateway", "discord.voice_client"):
        logging.getLogger(_vname).setLevel(logging.DEBUG)

# ────────────────────────────────────────────────────────────────────
# Environment & Configuration
# ────────────────────────────────────────────────────────────────────

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
OWNER_DISCORD_ID = os.getenv("OWNER_DISCORD_ID") or os.getenv("DISCORD_OWNER_ID")
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://127.0.0.1:9882")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
_RAW_GUILD_IDS = os.getenv("DISCORD_GUILD_IDS") or os.getenv("DISCORD_GUILD_ID") or ""
# Deferred TTS: text posts at brain-speed, audio attaches when synthesis lands.
# Env-controlled again (launch_koroki.ps1 passes true) — this was hardcoded False
# ("strict priority equivalence"), which silently ignored the launcher AND made every
# message wait out the full inline-TTS timeout whenever the adapter was slow/wedged
# (observed 123 s replies, 2026-07-03). Unset/false keeps the old simultaneous behavior.
DEFER_TTS_ENABLED = os.getenv("KOROKI_DEFER_TTS", "false").strip().lower() == "true"


def _parse_guild_ids(raw_ids: str) -> list[int]:
    out: list[int] = []
    for chunk in raw_ids.split(","):
        item = chunk.strip()
        if not item:
            continue
        if not item.isdigit():
            logger.warning("Skipping invalid guild ID in .env: %s", item)
            continue
        out.append(int(item))
    return out


DISCORD_GUILD_IDS = _parse_guild_ids(_RAW_GUILD_IDS)

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN not set in .env")
if not OWNER_DISCORD_ID:
    raise ValueError("OWNER_DISCORD_ID not set in .env")

try:
    OWNER_DISCORD_ID = int(OWNER_DISCORD_ID)
except ValueError:
    raise ValueError(f"OWNER_DISCORD_ID must be a valid integer, got: {OWNER_DISCORD_ID}")

logger.info("Discord Bot Configuration:")
logger.info(f"  DISCORD_TOKEN: ***{DISCORD_TOKEN[-12:]}")  # Last 12 chars for verification
logger.info(f"  OWNER_DISCORD_ID: {OWNER_DISCORD_ID}")
logger.info(f"  ORCHESTRATOR_URL: {ORCHESTRATOR_URL}")
logger.info(f"  KOROKI_DEFER_TTS: {DEFER_TTS_ENABLED}")
if DISCORD_CLIENT_ID:
    logger.info(f"  DISCORD_CLIENT_ID: {DISCORD_CLIENT_ID}")
if DISCORD_GUILD_IDS:
    logger.info(f"  DISCORD_GUILD_IDS: {','.join(str(gid) for gid in DISCORD_GUILD_IDS)}")
else:
    logger.info("  DISCORD_GUILD_IDS: (not set, using global command sync)")

# ────────────────────────────────────────────────────────────────────
# Discord Bot Setup
# ────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True  # Required to read message content

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

bot_lock = asyncio.Lock()
BOT_TIMEOUT_ENABLED = False
BASE_DIR = Path(__file__).resolve().parent
MEMORY_DIR = BASE_DIR / "data" / "memory"
REGAL_COLOR = discord.Color.from_rgb(193, 154, 107)

# Test-only guild — commands registered here never appear in production servers.
TEST_GUILD_ID = 1503131422553018408
_TEST_GUILD = discord.Object(id=TEST_GUILD_ID)

# Where she may talk (owner reopened the community 2026-07-08 — v3 validated,
# sleep gate + guillotine live). guild_id -> channel allowlist; None = every
# channel in that guild. Guilds absent here are ignored entirely. The
# mentions-only rule and owner gates still apply on top.
ALLOWED_GUILD_CHANNELS: dict[int, set[int] | None] = {
    TEST_GUILD_ID: None,                          # her home — all channels
    1453744803715092673: {1473935292837925061},   # community server — "Community AI" only
    1257681851296911360: None,                    # opened 2026-07-08 — all channels
}

# Both PRIVATE guilds get the full test-tool command set (owner 2026-07-08:
# 125… is his second private server — only him + picked people; the test guild
# has testers). All tools stay owner-gated at runtime regardless. Public
# servers (the community guild) get global commands only — never these.
_PRIVATE_GUILDS = [_TEST_GUILD, discord.Object(id=1257681851296911360)]

# Channel inside the test guild where testers interact with Koroki.
# Every message here + full bot evaluation data is written to a JSONL file
# so the dev can read it for analysis (see data/logs/test_channel.jsonl).
TEST_CHANNEL_ID = 1503131559019151410
_test_log_path = BASE_DIR / "data" / "logs" / "test_channel.jsonl"
_guild_activity_log_path = BASE_DIR / "data" / "logs" / "guild_activity.jsonl"

# Maps Discord user_id (str) → channel_id (int) for proactive message delivery.
# Populated on every message and PERSISTED across restarts (data/discord/channel_map.json) —
# the old in-memory-only map reset on every boot, which silenced ALL proactive delivery
# until each user messaged again (root cause of "she's never proactive", 2026-07-03).
_user_channel_map: dict[str, int] = {}
_CHANNEL_MAP_PATH = BASE_DIR / "data" / "discord" / "channel_map.json"


def _load_channel_map() -> None:
    global _last_owner_msg_at
    try:
        data = json.loads(_CHANNEL_MAP_PATH.read_text(encoding="utf-8"))
        channels = data.get("channels", data)  # tolerate the old flat format
        _user_channel_map.update({str(k): int(v) for k, v in channels.items()})
        if isinstance(data, dict) and data.get("last_owner_msg_ts"):
            _last_owner_msg_at = float(data["last_owner_msg_ts"])
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("[Outreach] channel map load failed: %s", exc)


def _save_channel_map() -> None:
    try:
        _CHANNEL_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CHANNEL_MAP_PATH.write_text(json.dumps({
            "channels": _user_channel_map,
            "last_owner_msg_ts": _last_owner_msg_at,
        }), encoding="utf-8")
    except Exception as exc:
        logger.warning("[Outreach] channel map save failed: %s", exc)

# Maps Discord message_id (int) → orchestrator request_id (str) for DPO labeling.
# Owner reacts 👍/👎 on a Koroki message → preference logged for DPO training.
# Capped at 500 entries (LRU-style: pop oldest when full).
_msg_to_request: dict[int, str] = {}
_MSG_CACHE_MAX = 500

# Organic reach-out tracking (owner only).
_last_owner_msg_at: float = 0.0       # epoch (wall-clock) seconds of last owner message
_last_proactive_sent_at: float = 0.0  # monotonic seconds of last organic reach-out
_last_greeted_absence: float = 0.0    # which absence (keyed by _last_owner_msg_at) got a greeting

# Shared-channel courtesy for proactive delivery: channel_id → (epoch ts, user_id) of the
# last human message. A reach-out aimed at user A must not land while user B is actively
# talking in the same channel — B answers on A's behalf and her per-user memory splits
# ("i didn't ask what happened", 2026-07-04). Events stay pending and expire on TTL.
_channel_last_human_msg: dict[int, tuple[float, str]] = {}
_PROACTIVE_BUSY_WINDOW_SEC = 600

_load_channel_map()

# ── Channel energy tracking ──────────────────────────────────────────
# Sliding window of message timestamps per channel (all non-bot messages).
# Deque keeps last 200 timestamps per channel — enough for 30-min rate calc.
_channel_timestamps: dict[int, collections.deque] = collections.defaultdict(
    lambda: collections.deque(maxlen=200)
)
# Recent message content per channel for topic classification (last 10 messages).
_channel_recent_msgs: dict[int, collections.deque] = collections.defaultdict(
    lambda: collections.deque(maxlen=10)
)
# When Koroki last sent a message in each channel.
_channel_koroki_last_spoke: dict[int, float] = {}

_PRESENCE_ENERGY_FILE = BASE_DIR / "data" / "presence" / "channel_energy.json"
_PRESENCE_CHECK_INTERVAL = random.uniform(60, 90)   # jittered 60-90s, reset each cycle


def _is_owner_id(user_id: int) -> bool:
    return int(user_id) == int(OWNER_DISCORD_ID)


async def _ensure_owner_interaction(interaction: discord.Interaction) -> bool:
    if _is_owner_id(interaction.user.id):
        return True
    await interaction.response.send_message(
        "Only Koro-san may command me in that way.",
        ephemeral=True,
    )
    return False


async def _ensure_owner_ctx(ctx: commands.Context) -> bool:
    if _is_owner_id(ctx.author.id):
        return True
    await ctx.send("Only Koro-san may command me in that way.")
    return False


def _memory_file_path(user_id: str | int) -> Path:
    return MEMORY_DIR / f"{str(user_id)}.json"


def _read_memory_payload(user_id: str | int) -> dict:
    path = _memory_file_path(user_id)
    if not path.exists():
        is_owner = _is_owner_id(int(user_id)) if str(user_id).isdigit() else False
        return {
            "relationship_score": 100 if is_owner else 0,
            "is_owner": is_owner,
            "last_summary": None,
            "core_facts": None,
            "recent_turns": [],
            "known_users": [],
            "relationship_message_counter": 0,
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_memory_payload(user_id: str | int, payload: dict) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path = _memory_file_path(user_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _iter_memory_file_user_ids() -> list[str]:
    if not MEMORY_DIR.exists():
        logger.warning("Memory directory does not exist at %s", MEMORY_DIR)
        return []
    out: list[str] = []
    for file in MEMORY_DIR.glob("*.json"):
        stem = file.stem.strip()
        if stem.isdigit():
            out.append(stem)
    return sorted(out)


def _clear_memory_fields(payload: dict) -> dict:
    payload["last_summary"] = None
    payload["core_facts"] = None
    payload["recent_turns"] = []
    payload["known_users"] = []
    return payload


def _reset_relationship_fields(payload: dict) -> dict:
    is_owner = bool(payload.get("is_owner", False))
    payload["relationship_score"] = 100 if is_owner else 0
    payload["relationship_message_counter"] = 0
    return payload


def _relationship_tier(score: int) -> str:
    if score >= 80:
        return "Devoted"
    if score >= 60:
        return "Cherished"
    if score >= 40:
        return "Trusted"
    if score >= 20:
        return "Acquainted"
    return "Stranger"


@bot.tree.command(name="ping", description="Check bot latency")
async def ping_slash(interaction: discord.Interaction):
    # everyone: harmless liveness check (owner ruling 2026-07-11)
    await interaction.response.send_message(
        f"Pong! Latency: {bot.latency * 1000:.0f}ms",
        ephemeral=True,
    )


@bot.tree.command(name="status", description="Check Orchestrator health")
@app_commands.default_permissions(administrator=True)  # hide from non-admins
async def status_slash(interaction: discord.Interaction):
    if not await _ensure_owner_interaction(interaction):
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            health = await client.get(f"{ORCHESTRATOR_URL}/health")
            if health.status_code == 200:
                data = health.json()
                await interaction.response.send_message(
                    f"Orchestrator online. Uptime: {data.get('uptime_seconds', '?')}s",
                    ephemeral=True,
                )
                return
    except Exception:
        pass
    await interaction.response.send_message("Orchestrator not responding", ephemeral=True)


@bot.tree.command(name="relationship", description="Check your relationship level with Koroki")
async def relationship_slash(interaction: discord.Interaction):
    payload = _read_memory_payload(str(interaction.user.id))
    score = int(payload.get("relationship_score", 0))
    tier = _relationship_tier(score)
    embed = discord.Embed(
        title="Relationship Ledger",
        description=f"{interaction.user.mention}, your standing in my court:",
        color=REGAL_COLOR,
    )
    embed.add_field(name="Score", value=f"{score}/100", inline=True)
    embed.add_field(name="Tier", value=tier, inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="relationship_check", description="Owner: inspect any user's relationship state")
@app_commands.default_permissions(administrator=True)  # hide from non-admins
@app_commands.describe(target="User to inspect")
async def relationship_check_slash(interaction: discord.Interaction, target: discord.User):
    if not await _ensure_owner_interaction(interaction):
        return
    payload = _read_memory_payload(str(target.id))
    score = int(payload.get("relationship_score", 0))
    counter = int(payload.get("relationship_message_counter", 0))
    embed = discord.Embed(
        title="Court Record Inspection",
        color=REGAL_COLOR,
    )
    embed.add_field(name="User", value=f"{target} ({target.id})", inline=False)
    embed.add_field(name="Relationship", value=f"{score}/100 ({_relationship_tier(score)})", inline=True)
    embed.add_field(name="Progress Counter", value=str(counter), inline=True)
    embed.add_field(name="Owner Flag", value=str(bool(payload.get("is_owner", False))), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="timeout", description="Owner: toggle Koroki message timeout")
@app_commands.default_permissions(administrator=True)  # hide from non-admins
@app_commands.describe(action="on/off/status")
@app_commands.choices(
    action=[
        app_commands.Choice(name="status", value="status"),
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
    ]
)
async def timeout_slash(interaction: discord.Interaction, action: app_commands.Choice[str]):
    if not await _ensure_owner_interaction(interaction):
        return
    global BOT_TIMEOUT_ENABLED
    if action.value == "status":
        await interaction.response.send_message(
            f"Timeout is currently {'ON' if BOT_TIMEOUT_ENABLED else 'OFF'}.",
            ephemeral=True,
        )
        return
    BOT_TIMEOUT_ENABLED = action.value == "on"
    await interaction.response.send_message(
        f"Timeout set to {'ON' if BOT_TIMEOUT_ENABLED else 'OFF'}.",
        ephemeral=True,
    )


@bot.tree.command(name="reset_memory", description="Owner: clear memory state for one user or everyone")
@app_commands.default_permissions(administrator=True)  # hide from non-admins
@app_commands.describe(target="Target user (omit when everyone=true)", everyone="Clear all users")
async def reset_memory_slash(
    interaction: discord.Interaction,
    target: Optional[discord.User] = None,
    everyone: bool = False,
):
    if not await _ensure_owner_interaction(interaction):
        return

    if everyone:
        count = 0
        for user_id in _iter_memory_file_user_ids():
            payload = _read_memory_payload(user_id)
            payload = _clear_memory_fields(payload)
            _write_memory_payload(user_id, payload)
            count += 1
        if count == 0:
            await interaction.response.send_message(
                f"No memory profiles found at {MEMORY_DIR}.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Cleared memory fields for {count} profile(s).",
            ephemeral=True,
        )
        return

    if target is None:
        await interaction.response.send_message("Pick a target or set everyone=true.", ephemeral=True)
        return

    payload = _read_memory_payload(str(target.id))
    payload = _clear_memory_fields(payload)
    _write_memory_payload(str(target.id), payload)
    await interaction.response.send_message(
        f"Memory fields cleared for {target.mention}.",
        ephemeral=True,
    )


@bot.tree.command(name="reset_relationship", description="Owner: reset relationship score for one user or everyone")
@app_commands.default_permissions(administrator=True)  # hide from non-admins
@app_commands.describe(target="Target user (omit when everyone=true)", everyone="Reset all non-owner users")
async def reset_relationship_slash(
    interaction: discord.Interaction,
    target: Optional[discord.User] = None,
    everyone: bool = False,
):
    if not await _ensure_owner_interaction(interaction):
        return

    if everyone:
        count = 0
        for user_id in _iter_memory_file_user_ids():
            payload = _read_memory_payload(user_id)
            payload = _reset_relationship_fields(payload)
            _write_memory_payload(user_id, payload)
            count += 1
        if count == 0:
            await interaction.response.send_message(
                f"No memory profiles found at {MEMORY_DIR}.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Relationship reset complete for {count} profile(s).",
            ephemeral=True,
        )
        return

    if target is None:
        await interaction.response.send_message("Pick a target or set everyone=true.", ephemeral=True)
        return

    payload = _read_memory_payload(str(target.id))
    payload = _reset_relationship_fields(payload)
    _write_memory_payload(str(target.id), payload)
    await interaction.response.send_message(
        f"Relationship reset for {target.mention}.",
        ephemeral=True,
    )


@bot.tree.command(name="mention", description="Owner: make Koroki greet/mention someone")
@app_commands.default_permissions(administrator=True)  # hide from non-admins
@app_commands.describe(target="User or bot to mention", greeting="Optional custom context for greeting")
async def mention_slash(interaction: discord.Interaction, target: discord.Member, greeting: str = ""):
    if not await _ensure_owner_interaction(interaction):
        return

    await interaction.response.defer(ephemeral=True)
    prompt = (
        f"Offer a short regal greeting to <@{target.id}> in 1-2 sentences. "
        f"If useful, incorporate this context: {greeting.strip() or 'none'}."
    )
    result = await query_orchestrator(
        user_id=str(interaction.user.id),
        message_content=prompt,
        is_owner=True,
        relationship_score=100,
        mentioned_user_ids=[str(target.id)],
    )

    text = (result or {}).get("text", "A greeting from my court to yours.").strip()
    await interaction.channel.send(f"{target.mention} {text}")
    await interaction.followup.send("Mention delivered.", ephemeral=True)


@bot.tree.command(name="help", description="See Koroki's introduction and available commands")
async def help_slash(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Koroki, Princess of Quiet Courts",
        description=(
            "I am Koroki. Regal, teasing, and occasionally merciful.\n"
            "Koro-san is my creator and the one I hold dearest."
        ),
        color=REGAL_COLOR,
    )
    embed.add_field(
        name="Everyone",
        value="`/help` `!help`\n`/relationship` `!relationship`\n`/sing` — make Koroki sing a song",
        inline=False,
    )
    embed.add_field(
        name="Owner Only",
        value=(
            "`/ping` `!ping`\n"
            "`/status` `!status`\n"
            "`/timeout` `!timeout`\n"
            "`/relationship_check` `!relationshipcheck`\n"
            "`/reset_memory` `!resetmemory`\n"
            "`/reset_relationship` `!resetrelationship`\n"
            "`/mention` `!mention`"
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ────────────────────────────────────────────────────────────────────
# Singing Slash Command
# ────────────────────────────────────────────────────────────────────

# /sing daily rate limits (owner ruling 2026-07-11): everyone gets N/day, a
# subscriber Discord role gets more, owner is unlimited. Counted per UTC day;
# usage recorded at accept-time (a rare pipeline failure costs a slot — owner
# can /reset if needed). Config: settings.yaml singing.rate_limits.
SING_USAGE_FILE = BASE_DIR / "data" / "discord" / "sing_usage.json"


def _sing_limits() -> dict:
    from shared.utils.config import get_settings

    rl = (get_settings().get("singing") or {}).get("rate_limits") or {}
    return {
        "everyone": int(rl.get("everyone_per_day", 5)),
        "subscriber": int(rl.get("subscriber_per_day", 25)),
        "role": str(rl.get("subscriber_role", "Koroki+")),
    }


def _sing_usage_today() -> tuple[str, dict]:
    """(utc_day_key, {user_id: count}) — only today's bucket is retained."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        data = json.loads(SING_USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return today, dict(data.get(today) or {})


def _sing_record_use(user_id: str) -> None:
    today, bucket = _sing_usage_today()
    bucket[user_id] = int(bucket.get(user_id, 0)) + 1
    try:
        SING_USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        # write ONLY today's bucket — old days self-prune
        SING_USAGE_FILE.write_text(json.dumps({today: bucket}), encoding="utf-8")
    except Exception:
        logger.warning("sing usage write failed", exc_info=True)


def _sing_tier_limit(interaction: discord.Interaction) -> tuple[Optional[int], str]:
    """(daily_limit or None for unlimited, tier_label)."""
    limits = _sing_limits()
    if _is_owner_id(interaction.user.id):
        return None, "owner"
    roles = getattr(interaction.user, "roles", []) or []
    if any(getattr(r, "name", "") == limits["role"] for r in roles):
        return limits["subscriber"], "subscriber"
    return limits["everyone"], "everyone"


async def _sing_rate_ok(interaction: discord.Interaction) -> bool:
    """Enforce the daily sing cap; reject (ephemeral) + return False if over."""
    limit, tier = _sing_tier_limit(interaction)
    if limit is None:
        return True  # owner: unlimited
    _, bucket = _sing_usage_today()
    if int(bucket.get(str(interaction.user.id), 0)) >= limit:
        hint = ("" if tier == "subscriber"
                else f" (subscribers get {_sing_limits()['subscriber']}/day)")
        await interaction.response.send_message(
            f"mm, that's all {limit} songs you get today~ come back tomorrow.{hint}",
            ephemeral=True,
        )
        return False
    return True


def _sing_intro_text(song: str, relationship_score: int) -> str:
    if relationship_score >= 80:
        options = [
            f"fine fine, **{song}**... only because you asked.",
            f"you really like this one, huh. okay.",
            f"*sighs* alright. **{song}**.",
        ]
    elif relationship_score >= 50:
        options = [
            f"seriously? fine. **{song}**.",
            f"you and this song. whatever.",
            f"ugh, again? okay.",
        ]
    else:
        options = [
            f"...**{song}**. don't make it a habit.",
            f"i'll do it. just this once.",
            f"fine.",
        ]
    return random.choice(options)


async def _animate_singing_stages(
    msg: discord.Message,
    song: str,
    done: asyncio.Event,
    intro: str,
) -> None:
    stages = [
        (12,  f"finding **{song}**"),
        (60,  "downloading audio"),
        (130, "separating vocals"),
        (260, "synthesizing voice"),
        (420, "mixing track"),
        (600, "almost ready"),
    ]
    elapsed = 0
    for delay, label in stages:
        try:
            await asyncio.wait_for(done.wait(), timeout=delay - elapsed)
            return
        except asyncio.TimeoutError:
            elapsed = delay
            try:
                await msg.edit(content=f"{intro}\n-# {label}...")
            except Exception:
                pass
    # Past all stages — cycle dots until done
    while not done.is_set():
        for dots in [".", "..", "..."]:
            try:
                await asyncio.wait_for(done.wait(), timeout=6)
                return
            except asyncio.TimeoutError:
                try:
                    await msg.edit(content=f"{intro}\n-# almost ready{dots}")
                except Exception:
                    pass


@bot.tree.command(name="sing", description="Make Koroki sing a song")
@app_commands.describe(
    song="Song name (e.g. 'Never Gonna Give You Up by Rick Astley')",
    transpose="Pitch shift in semitones (default 0)",
    vocal_only="Skip the instrumental backing track",
)
async def sing_slash(
    interaction: discord.Interaction,
    song: str,
    transpose: int = 0,
    vocal_only: bool = False,
):
    import base64 as _b64, tempfile, os as _os
    # Tiered daily cap (owner unlimited / subscriber role / everyone) — checked
    # BEFORE the first response so the rejection is the interaction's reply.
    if not await _sing_rate_ok(interaction):
        return
    user_id = str(interaction.user.id)
    _sing_record_use(user_id)
    payload = _read_memory_payload(user_id)
    rel_score = int(payload.get("relationship_score", 50))
    intro = _sing_intro_text(song, rel_score)

    await interaction.response.send_message(content=f"{intro}\n-# finding **{song}**...")
    msg = await interaction.original_response()

    done = asyncio.Event()
    pipeline_result: dict = {}
    pipeline_error: list = []

    async def _run_pipeline() -> None:
        try:
            async with httpx.AsyncClient(timeout=840.0) as client:
                resp = await client.post(
                    f"{ORCHESTRATOR_URL}/v1/sing",
                    json={"song": song, "user_id": user_id, "transpose": transpose, "vocal_only": vocal_only},
                )
                resp.raise_for_status()
                pipeline_result.update(resp.json())
        except Exception as exc:
            pipeline_error.append(exc)
        finally:
            done.set()

    pipeline_task = asyncio.create_task(_run_pipeline())
    await _animate_singing_stages(msg, song, done, intro)
    await pipeline_task

    if pipeline_error:
        exc = pipeline_error[0]
        detail = (
            exc.response.json().get("detail", str(exc))
            if hasattr(exc, "response") and exc.response.content
            else str(exc)
        )
        await msg.edit(content=f"{intro}\n-# failed: {detail}")
        return

    await msg.edit(content=intro)

    wav_bytes = _b64.b64decode(pipeline_result["wav_base64"])
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        tmp_path = f.name

    try:
        file = discord.File(tmp_path, filename=f"koroki_{song[:30].replace(' ', '_')}.wav")
        await interaction.followup.send(file=file)
    finally:
        try:
            _os.unlink(tmp_path)
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────────
# Chess Slash Commands
# ────────────────────────────────────────────────────────────────────

chess_group = app_commands.Group(name="chess", description="Play chess with Koroki")
bot.tree.add_command(chess_group)


def _chess_outcome_line(status: str) -> str:
    return {
        "user_won": "You won. I'll remember that.",
        "koroki_won": "I win.",
        "draw": "Draw.",
        "resigned": "You resigned.",
    }.get(status, "")


def _chess_board_file(data: dict) -> discord.File | None:
    """Rendered board PNG (last move highlighted) from the orchestrator, if present."""
    b64 = data.get("board_png_b64")
    if not b64:
        return None
    try:
        return discord.File(io.BytesIO(base64.b64decode(b64)), filename="board.png")
    except Exception as exc:
        logger.warning("[Chess] board image decode failed: %s", exc)
        return None


@chess_group.command(name="start", description="Challenge Koroki to a chess game")
@app_commands.describe(color="Your piece color")
@app_commands.choices(color=[
    app_commands.Choice(name="white (you go first)", value="white"),
    app_commands.Choice(name="black (Koroki goes first)", value="black"),
])
async def chess_start_cmd(
    interaction: discord.Interaction,
    color: app_commands.Choice[str] = None,
):
    user_id = str(interaction.user.id)
    payload = _read_memory_payload(user_id)
    chosen_color = color.value if color else "white"

    await interaction.response.send_message(content="chess. fine.\n-# setting up board...")
    msg = await interaction.original_response()

    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            resp = await client.post(f"{ORCHESTRATOR_URL}/v1/games/chess/start", json={
                "user_id": user_id,
                "is_owner": _is_owner_id(interaction.user.id),
                "relationship_score": int(payload.get("relationship_score", 0)),
                "color": chosen_color,
            })
    except Exception as exc:
        logger.error("[Chess] start request failed: %s", exc)
        await msg.edit(content="chess. fine.\ncould not reach the game server.")
        return

    if resp.status_code != 200:
        await msg.edit(content="chess. fine.\nfailed to start game.")
        return

    data = resp.json()
    lines: list[str] = []
    if data.get("commentary"):
        lines.append(data["commentary"])
    if data.get("first_koroki_move"):
        lines.append(f"-# Koroki opens with **{data['first_koroki_move']}**")
    board_file = _chess_board_file(data)
    if board_file is None:
        lines.append(f"```\n{data.get('board_ascii', '')}\n```")
    lines.append(f"-# You are {'White' if chosen_color == 'white' else 'Black'}. Use `/chess move` to play.")
    await msg.edit(content="\n".join(lines), attachments=[board_file] if board_file else [])


@chess_group.command(name="move", description="Make your chess move")
@app_commands.describe(move="Move in UCI (e2e4) or SAN (e4, Nf3) notation")
async def chess_move_cmd(interaction: discord.Interaction, move: str):
    user_id = str(interaction.user.id)
    payload = _read_memory_payload(user_id)

    await interaction.response.send_message(content=f"*considers `{move}`*\n-# thinking...")
    msg = await interaction.original_response()

    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            resp = await client.post(f"{ORCHESTRATOR_URL}/v1/games/chess/move", json={
                "user_id": user_id,
                "is_owner": _is_owner_id(interaction.user.id),
                "relationship_score": int(payload.get("relationship_score", 0)),
                "move": move.strip(),
            })
    except Exception as exc:
        logger.error("[Chess] move request failed: %s", exc)
        await msg.edit(content="game server unreachable.")
        return

    if resp.status_code == 400:
        detail = resp.json().get("detail", "illegal_move")
        err = {
            "no_active_game": "No active game. Use `/chess start` first.",
            "game_over": "This game is already over.",
            "not_your_turn": "It's not your turn.",
            "illegal_move": f"`{move}` is not a legal move. Check your notation.",
        }.get(detail, f"Move rejected: {detail}")
        await msg.edit(content=err)
        return

    if resp.status_code != 200:
        await msg.edit(content="something went wrong.")
        return

    data = resp.json()
    lines: list[str] = []
    if data.get("commentary"):
        lines.append(data["commentary"])
    # Mechanical move line — the human must always see WHAT she played, whether
    # or not she chose to comment on it (owner, 2026-07-04).
    if data.get("koroki_move"):
        _desc = data.get("koroki_move_desc")
        lines.append(
            f"-# Koroki played **{data['koroki_move']}**" + (f" — {_desc}" if _desc else "")
        )
    if data.get("in_check") and data.get("status") == "active":
        lines.append("*Check.*")
    outcome = _chess_outcome_line(data.get("status", "active"))
    if outcome:
        lines.append(outcome)
    board_file = _chess_board_file(data)
    if board_file is None:
        lines.append(f"```\n{data.get('board_ascii', '')}\n```")
    await msg.edit(content="\n".join(lines), attachments=[board_file] if board_file else [])


@chess_group.command(name="board", description="Show the current board")
async def chess_board_cmd(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{ORCHESTRATOR_URL}/v1/games/chess/state",
                params={"user_id": user_id},
            )
    except Exception as exc:
        logger.error("[Chess] state request failed: %s", exc)
        await interaction.response.send_message("Game server unreachable.", ephemeral=True)
        return

    if resp.status_code == 404:
        await interaction.response.send_message(
            "No active game. Use `/chess start` to begin.", ephemeral=True
        )
        return

    data = resp.json()
    turn_label = "Your turn." if data.get("is_users_turn") else "Waiting on Koroki..."
    move_num = data.get("move_count", 0) // 2 + 1
    board_file = _chess_board_file(data)
    if board_file is not None:
        await interaction.response.send_message(
            f"Move {move_num} — {turn_label}", file=board_file, ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"Move {move_num} — {turn_label}\n```\n{data.get('board_ascii', '')}\n```",
            ephemeral=True,
        )


@chess_group.command(name="resign", description="Resign from the current game")
async def chess_resign_cmd(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    payload = _read_memory_payload(user_id)

    await interaction.response.send_message(content="*steps away from the board*\n-# processing...")
    msg = await interaction.original_response()

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(f"{ORCHESTRATOR_URL}/v1/games/chess/resign", json={
                "user_id": user_id,
                "is_owner": _is_owner_id(interaction.user.id),
                "relationship_score": int(payload.get("relationship_score", 0)),
            })
    except Exception as exc:
        logger.error("[Chess] resign request failed: %s", exc)
        await msg.edit(content="game server unreachable.")
        return

    if resp.status_code == 400:
        await msg.edit(content="No active game to resign.")
        return

    data = resp.json()
    await msg.edit(content=data.get("commentary") or "Game over.")


# ────────────────────────────────────────────────────────────────────
# Minecraft Slash Commands (owner-only)
# ────────────────────────────────────────────────────────────────────

_minecraft_proc: asyncio.subprocess.Process | None = None
# Supervision state (2026-07-14): the node process can die SILENTLY (native crash)
# or wedge with a blocked event loop (pathfinder A* storm) — in both cases its own
# JS-level guards are dead. The parent must supervise: index.js prints "[MC] hb"
# every 45s; no output for _MC_STALL_S seconds => kill + restart. Process exit
# (returncode set) => restart. Rate-limited so a broken server can't loop forever.
_mc_desired: bool = False              # owner wants her in-game (join sets, leave clears)
_mc_spawn_env: dict | None = None      # env of the last join (for restarts)
_mc_channel: discord.abc.Messageable | None = None
_mc_last_output: float = 0.0
_mc_restarts: list[float] = []         # timestamps of supervisor restarts
_mc_supervisor_task: asyncio.Task | None = None
_MC_STALL_S = 120
_MC_MAX_RESTARTS = 8                   # per hour, then give up loudly

minecraft_group = app_commands.Group(
    name="minecraft", description="Koroki's Minecraft player client",
    default_permissions=discord.Permissions(administrator=True),  # hide from non-admins
)
bot.tree.add_command(minecraft_group)

MC_BOT_DIR = BASE_DIR / "clients" / "minecraft-bot"


async def _mc_log_relay(proc: asyncio.subprocess.Process, channel: discord.abc.Messageable) -> None:
    """Read stdout from the Minecraft bot and relay key lines to Discord."""
    global _mc_last_output
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            _mc_last_output = time.time()
            if text == "[MC] hb":
                continue  # liveness heartbeat — supervisor food, not chat content
            logger.info("[MC] %s", text)
            # Only surface lines that start with our own markers to avoid chat spam.
            if text.startswith("[MC]") or text.startswith("[BrainClient]"):
                try:
                    await channel.send(f"*{text}*")
                except Exception:
                    pass
    except Exception as exc:
        logger.error("[MC] Log relay stopped: %s", exc)
    finally:
        logger.info("[MC] Log relay ended.")


async def _mc_start_process() -> bool:
    """(Re)start the Minecraft bot with the saved env; wire up its log relay."""
    global _minecraft_proc, _mc_last_output
    if not _mc_spawn_env or not _mc_channel:
        return False
    try:
        _minecraft_proc = await asyncio.create_subprocess_exec(
            "node", "index.js",
            cwd=str(MC_BOT_DIR),
            env=_mc_spawn_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as exc:
        logger.error("[MC] Failed to start bot: %s", exc)
        _minecraft_proc = None
        return False
    _mc_last_output = time.time()
    asyncio.create_task(_mc_log_relay(_minecraft_proc, _mc_channel), name="koroki-mc-log")
    logger.info("[MC] Minecraft bot started (PID %d)", _minecraft_proc.pid)
    return True


async def _mc_supervisor() -> None:
    """Kill-and-restart the bot when it dies silently or wedges (no output)."""
    global _minecraft_proc, _mc_restarts
    while True:
        await asyncio.sleep(20)
        if not _mc_desired or _minecraft_proc is None:
            continue
        died = _minecraft_proc.returncode is not None
        stalled = (time.time() - _mc_last_output) > _MC_STALL_S
        if not died and not stalled:
            continue
        # rate limit: give up (loudly) if she keeps dying — likely the server itself
        now = time.time()
        _mc_restarts = [t for t in _mc_restarts if now - t < 3600]
        if len(_mc_restarts) >= _MC_MAX_RESTARTS:
            if _mc_channel:
                try:
                    await _mc_channel.send(
                        "*[MC] supervisor: too many restarts this hour — giving up. "
                        "Use `/minecraft join` when the server is healthy.*"
                    )
                except Exception:
                    pass
            logger.error("[MC] supervisor: restart limit hit — standing down.")
            return
        _mc_restarts.append(now)
        reason = "process died" if died else f"no output for {_MC_STALL_S}s (wedged)"
        logger.warning("[MC] supervisor: %s — restarting (%d this hour)", reason, len(_mc_restarts))
        if _mc_channel:
            try:
                await _mc_channel.send(f"*[MC] supervisor: {reason} — restarting her client*")
            except Exception:
                pass
        if not died:
            try:
                _minecraft_proc.kill()
                await asyncio.wait_for(_minecraft_proc.wait(), timeout=10)
            except Exception:
                pass
        await _mc_start_process()


@minecraft_group.command(name="join", description="Owner: make Koroki join a Minecraft server")
@app_commands.describe(
    server="Server address — host or host:port (default port 25565)",
    version="Minecraft version to force, e.g. 1.21.4 (leave blank to auto-detect with fallback)",
)
async def mc_join_cmd(interaction: discord.Interaction, server: str = "localhost", version: str = ""):
    if not await _ensure_owner_interaction(interaction):
        return

    global _minecraft_proc, _mc_desired, _mc_spawn_env, _mc_channel, _mc_supervisor_task, _mc_restarts

    if _minecraft_proc and _minecraft_proc.returncode is None:
        await interaction.response.send_message(
            "Already in a Minecraft server. Use `/minecraft leave` first.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    if not (MC_BOT_DIR / "node_modules").exists():
        await interaction.followup.send(
            "Minecraft bot not set up. Run `npm install` in `clients/minecraft-bot/`.", ephemeral=True
        )
        return

    parts = server.strip().rsplit(":", 1)
    host = parts[0] or "localhost"
    port = parts[1] if len(parts) > 1 and parts[1].isdigit() else "25565"

    env = os.environ.copy()
    env["MC_SERVER_HOST"] = host
    env["MC_SERVER_PORT"] = port
    env["ORCHESTRATOR_URL"] = ORCHESTRATOR_URL
    env["MC_USERNAME"] = "Koroki"
    env["MC_AUTH"] = "offline"
    if version.strip():
        env["MC_VERSION"] = version.strip()

    _mc_spawn_env = env
    _mc_channel = interaction.channel
    _mc_restarts = []
    _mc_desired = True

    ok = await _mc_start_process()
    if not ok:
        _mc_desired = False
        await interaction.followup.send(
            "Failed to start the Minecraft bot (is Node.js installed and in PATH?).", ephemeral=True
        )
        return

    # one supervisor for the whole session — restarts her on silent death / wedge
    if _mc_supervisor_task is None or _mc_supervisor_task.done():
        _mc_supervisor_task = asyncio.create_task(_mc_supervisor(), name="koroki-mc-supervisor")

    ver_note = f" (version: {version.strip()})" if version.strip() else " (auto-detect with fallback)"
    await interaction.followup.send(
        f"Koroki is connecting to `{host}:{port}`{ver_note}... (supervised: auto-restarts on crash/wedge)",
        ephemeral=True,
    )


@minecraft_group.command(name="leave", description="Owner: disconnect Koroki from Minecraft")
async def mc_leave_cmd(interaction: discord.Interaction):
    if not await _ensure_owner_interaction(interaction):
        return

    global _minecraft_proc, _mc_desired

    _mc_desired = False  # supervisor: stand down — this exit is intentional

    if not _minecraft_proc or _minecraft_proc.returncode is not None:
        await interaction.response.send_message("No active Minecraft session.", ephemeral=True)
        return

    _minecraft_proc.terminate()
    _minecraft_proc = None
    await interaction.response.send_message("Koroki disconnected from Minecraft.", ephemeral=True)
    logger.info("[MC] Minecraft bot terminated by owner.")


@minecraft_group.command(name="status", description="Check Koroki's Minecraft connection")
async def mc_status_cmd(interaction: discord.Interaction):
    active = _minecraft_proc is not None and _minecraft_proc.returncode is None
    pid = _minecraft_proc.pid if active else None
    msg = f"Minecraft: **{'connected'if active else 'offline'}**"
    if pid:
        msg += f" (PID {pid})"
    await interaction.response.send_message(msg, ephemeral=True)


# ────────────────────────────────────────────────────────────────────
# Test-Guild Slash Commands (guild 1503131422553018408 only)
# These commands never appear in production servers.
# ────────────────────────────────────────────────────────────────────


@bot.tree.command(
    name="test_scene",
    description="[TEST] Send a message with forced context and show full debug output",
    guilds=_PRIVATE_GUILDS,
)
@app_commands.describe(
    message="Message to send to Koroki",
    relationship="Override relationship score 0–100 (omit to use stored value)",
    as_owner="Treat as owner interaction",
)
async def test_scene_cmd(
    interaction: discord.Interaction,
    message: str,
    relationship: int = -1,
    as_owner: bool = False,
) -> None:
    await interaction.response.defer(ephemeral=True)

    user_id = str(interaction.user.id)
    if as_owner:
        rel_score = 100
        is_owner_flag = True
    elif relationship >= 0:
        rel_score = min(100, max(0, relationship))
        is_owner_flag = False
    else:
        stored = _read_memory_payload(user_id)
        rel_score = int(stored.get("relationship_score", 0))
        is_owner_flag = _is_owner_id(interaction.user.id)

    # Use a fresh test user_id so stored memory can't interfere with forced relationship.
    test_uid = f"test_{int(datetime.now().timestamp() * 1000)}"
    request_id = f"testscene_{test_uid}"

    user_context = {
        "user_id": test_uid,
        "relationship_score": rel_score,
        "is_owner": is_owner_flag,
        "mode": "owner" if is_owner_flag else "auto",
        "platform": "discord",
        "mentioned_user_ids": [],
    }
    payload = {
        "request_id": request_id,
        "message": message,
        "user_context": user_context,
        "defer_tts": True,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{ORCHESTRATOR_URL}/v1/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        await interaction.followup.send(f"Orchestrator error: {exc}", ephemeral=True)
        return

    text = data.get("text", "").strip()
    ev = data.get("evaluation", {})
    timings = data.get("timings", {}) or {}

    # ── Response embed ──────────────────────────────────────────────
    embed = discord.Embed(title="Test Scene Result", color=discord.Color.blurple())
    embed.add_field(name="Context", value=f"score={rel_score} owner={is_owner_flag}", inline=True)
    embed.add_field(
        name="Adapter",
        value=data.get("adapter_used") or "base",
        inline=True,
    )

    truncated_text = (text[:497] + "...") if len(text) > 500 else text
    embed.add_field(name="Response", value=truncated_text or "(empty)", inline=False)

    # ── Emotion ─────────────────────────────────────────────────────
    cur_emo = ev.get("current_emotion", "?")
    exp_emo = ev.get("expressed_emotion") or "—"
    diverged = ev.get("emotion_diverged", False)
    intensity = ev.get("emotion_intensity", "?")
    emo_line = f"{cur_emo} (intensity {intensity}) | expressed: {exp_emo} | diverged: {'YES' if diverged else 'no'}"
    embed.add_field(name="Emotion", value=emo_line, inline=False)

    av = ev.get("affect_vector") or {}
    if av:
        av_parts = [f"{k}={v}" for k, v in sorted(av.items())]
        av_str = "  ".join(av_parts)
        embed.add_field(name="Affect Vector", value=av_str or "—", inline=False)

    # ── Reflection issues ───────────────────────────────────────────
    issues = ev.get("reflection_issues") or []
    embed.add_field(
        name="Reflection Issues",
        value=", ".join(issues) if issues else "none",
        inline=True,
    )

    # ── Thinking mode ───────────────────────────────────────────────
    embed.add_field(
        name="Thinking Mode",
        value="ENABLED" if ev.get("thinking_enabled") else "off",
        inline=True,
    )

    # ── Cognition scores ────────────────────────────────────────────
    cog_lines = [
        f"coherence={_fmt(ev.get('cognition_coherence_score'))}",
        f"affect_stability={_fmt(ev.get('cognition_affect_stability'))}",
        f"intent={_fmt(ev.get('cognition_intent_strength'))}",
        f"drive={_fmt(ev.get('cognition_initiative_drive'))}",
        f"proactive_eligible={ev.get('cognition_proactive_eligible', '?')}",
    ]
    embed.add_field(name="Cognition", value="  ".join(cog_lines), inline=False)

    # ── TTS ─────────────────────────────────────────────────────────
    tts_lines = [
        f"auto_tags={ev.get('tts_auto_tags_inferred', '—')}",
        f"explicit_applied={ev.get('tts_explicit_tags_applied', 0)}",
        f"action_chars_filtered={ev.get('tts_action_chars_filtered', 0)}",
    ]
    embed.add_field(name="TTS", value="  ".join(tts_lines), inline=False)

    # ── Latency ─────────────────────────────────────────────────────
    t_total = timings.get("t_total_ms")
    t_brain = timings.get("t_brain_first_token_ms")
    t_tts = timings.get("t_tts_first_chunk_ms")
    lat = f"total={t_total}ms  brain={t_brain}ms  tts={t_tts}ms"
    embed.add_field(name="Latency", value=lat, inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)


def _fmt(val) -> str:
    if val is None:
        return "?"
    try:
        return f"{float(val):.2f}"
    except (TypeError, ValueError):
        return str(val)


@bot.tree.command(
    name="test_emotion_state",
    description="[TEST] Dump stored emotion state for a user",
    guilds=_PRIVATE_GUILDS,
)
@app_commands.describe(target="User to inspect (omit for yourself)")
async def test_emotion_state_cmd(
    interaction: discord.Interaction,
    target: Optional[discord.User] = None,
) -> None:
    if not await _ensure_owner_interaction(interaction):
        return
    uid = str(target.id) if target else str(interaction.user.id)
    payload = _read_memory_payload(uid)
    emo = payload.get("emotional_state") or {}
    av = emo.get("affect_vector") or {}
    sm = emo.get("slow_mood") or {}
    pf = emo.get("private_feelings") or []

    embed = discord.Embed(title=f"Emotion State — user {uid}", color=REGAL_COLOR)
    embed.add_field(
        name="Current",
        value=(
            f"emotion={emo.get('current_emotion','?')}  "
            f"intensity={emo.get('intensity','?')}  "
            f"mood={emo.get('mood_state','?')}"
        ),
        inline=False,
    )
    if av:
        embed.add_field(
            name="Affect Vector (fast)",
            value="  ".join(f"{k}={v}" for k, v in sorted(av.items())),
            inline=False,
        )
    if sm:
        embed.add_field(
            name="Slow Mood (baseline)",
            value="  ".join(f"{k}={v}" for k, v in sorted(sm.items())),
            inline=False,
        )
    if pf:
        embed.add_field(name="Private Feelings", value="\n".join(pf), inline=False)
    rel = payload.get("relationship_score", "?")
    embed.add_field(name="Relationship Score", value=str(rel), inline=True)
    last = emo.get("last_updated", "?")
    embed.add_field(name="Last Updated", value=last, inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="test_proactive",
    description="[TEST] Show autonomy scheduler drive metrics and proactive eligibility",
    guilds=_PRIVATE_GUILDS,
)
async def test_proactive_cmd(interaction: discord.Interaction) -> None:
    if not await _ensure_owner_interaction(interaction):
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{ORCHESTRATOR_URL}/v1/autonomy/status")
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        await interaction.response.send_message(f"Autonomy status error: {exc}", ephemeral=True)
        return

    last_tick = data.get("last_tick") or {}
    embed = discord.Embed(title="Autonomy Scheduler Status", color=discord.Color.orange())
    embed.add_field(name="Running", value=str(data.get("running", "?")), inline=True)
    embed.add_field(name="Task Active", value=str(data.get("task_active", "?")), inline=True)
    tick_ts = last_tick.get("tick_started_at")
    if isinstance(tick_ts, (int, float)) and tick_ts > 0:
        tick_str = datetime.fromtimestamp(tick_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        tick_str = str(tick_ts) if tick_ts else "not yet"
    embed.add_field(name="Last Tick At", value=tick_str, inline=False)
    embed.add_field(name="Scanned Users", value=str(last_tick.get("scanned_users", 0)), inline=True)
    embed.add_field(name="Events Generated", value=str(last_tick.get("generated_events", 0)), inline=True)
    embed.add_field(name="Flushed Records", value=str(last_tick.get("flushed_records", 0)), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="test_dpo",
    description="[TEST] Show DPO preference log stats",
    guilds=_PRIVATE_GUILDS,
)
async def test_dpo_cmd(interaction: discord.Interaction) -> None:
    if not await _ensure_owner_interaction(interaction):
        return
    import json as _json

    responses_path = BASE_DIR / "data" / "dpo_preferences" / "responses.jsonl"
    labels_path = BASE_DIR / "data" / "dpo_preferences" / "labels.json"

    total_logged = 0
    diverged_count = 0
    if responses_path.exists():
        for line in responses_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = _json.loads(line)
                total_logged += 1
                if entry.get("emotion_diverged"):
                    diverged_count += 1
            except Exception:
                pass

    labels: dict = {}
    if labels_path.exists():
        try:
            labels = _json.loads(labels_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    chosen = sum(1 for v in labels.values() if v == "chosen")
    rejected = sum(1 for v in labels.values() if v == "rejected")

    embed = discord.Embed(title="DPO Preference Log Stats", color=discord.Color.green())
    embed.add_field(name="Responses Logged", value=str(total_logged), inline=True)
    embed.add_field(name="Emotion Diverged", value=str(diverged_count), inline=True)
    embed.add_field(name="Labels Total", value=str(len(labels)), inline=True)
    embed.add_field(name="Chosen (👍)", value=str(chosen), inline=True)
    embed.add_field(name="Rejected (👎)", value=str(rejected), inline=True)
    pairs_ready = min(chosen, rejected)
    embed.add_field(name="DPO Pairs Ready", value=f"{pairs_ready} (need 200+ to train)", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ────────────────────────────────────────────────────────────────────
# Voice-channel presence + watch-party (STREAMING & PLAY Stage 0 + 1)
# Video = owner's account clicks Go Live; her voice = this bot in the VC.
# ────────────────────────────────────────────────────────────────────

_vc_play_queue: asyncio.Queue = asyncio.Queue()
_vc_player_task: asyncio.Task | None = None
_watch_session = None          # stream_watch.WatchSession | None
_watch_text_channel: discord.abc.Messageable | None = None
_watch_game: str | None = None


async def _get_vc() -> discord.VoiceClient | None:
    for vc in bot.voice_clients:
        if vc.is_connected():
            return vc
    return None


async def _vc_player_loop() -> None:
    """Plays queued wavs into the current VC, one at a time."""
    while True:
        wav_path = await _vc_play_queue.get()
        vc = await _get_vc()
        if vc is None:
            logger.warning("[VC] audio queued but not connected — dropping: %s", wav_path)
            continue
        done = asyncio.Event()

        def _after(err, _done=done):
            if err:
                logger.error("[VC] playback error: %s", err)
            bot.loop.call_soon_threadsafe(_done.set)

        try:
            vc.play(discord.FFmpegPCMAudio(wav_path), after=_after)
            await done.wait()
        except Exception as exc:
            logger.error("[VC] play failed: %s", exc)


async def _vc_play(wav_path: str) -> None:
    global _vc_player_task
    if _vc_player_task is None or _vc_player_task.done():
        _vc_player_task = asyncio.create_task(_vc_player_loop(), name="koroki-vc-player")
    await _vc_play_queue.put(wav_path)


def _watch_settings() -> dict:
    try:
        from shared.utils.config import get_settings
        return dict((get_settings().get("streaming") or {}).get("watch") or {})
    except Exception:
        return {}


@bot.tree.command(name="vc_join", description="[STREAM] Koroki joins your voice channel", guilds=_PRIVATE_GUILDS)
async def vc_join_cmd(interaction: discord.Interaction) -> None:
    if not await _ensure_owner_interaction(interaction):
        return
    state = getattr(interaction.user, "voice", None)
    if state is None or state.channel is None:
        await interaction.response.send_message("join a voice channel first", ephemeral=True)
        return
    # Voice connect takes >3 s (gateway handshake + DAVE) — defer or Discord
    # shows "application did not respond".
    await interaction.response.defer(ephemeral=True)
    try:
        existing = await _get_vc()
        if existing:
            await existing.move_to(state.channel)
        else:
            # VoiceRecvClient = VoiceClient + receive support (her ears can
            # attach without reconnecting; playback path is unchanged).
            try:
                from discord.ext import voice_recv
                await state.channel.connect(timeout=20.0, cls=voice_recv.VoiceRecvClient)
            except ImportError:
                await state.channel.connect(timeout=20.0)
    except Exception as exc:
        logger.error("[VC] join failed: %s", exc, exc_info=True)
        await interaction.followup.send(f"couldn't join voice: {type(exc).__name__}: {exc}", ephemeral=True)
        return
    await interaction.followup.send(f"in {state.channel.name}. say the word.", ephemeral=True)


@bot.tree.command(name="vc_leave", description="[STREAM] Koroki leaves voice", guilds=_PRIVATE_GUILDS)
async def vc_leave_cmd(interaction: discord.Interaction) -> None:
    if not await _ensure_owner_interaction(interaction):
        return
    vc = await _get_vc()
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("left voice.", ephemeral=True)
    else:
        await interaction.response.send_message("not in voice.", ephemeral=True)


_ears_session = None           # ears.EarsSession | None
_ears_text_channel: discord.abc.Messageable | None = None


async def _on_heard_phrase(phrase) -> None:
    """A finished spoken phrase from the VC → same pipeline as a typed message."""
    is_owner = int(phrase.user_id) == int(OWNER_DISCORD_ID)
    result = await query_orchestrator(
        user_id=str(phrase.user_id),
        message_content=phrase.text,
        is_owner=is_owner,
    )
    if result is None:
        logger.warning("[ears] orchestrator gave no reply for: %s", phrase.text[:60])
        return
    reply = (result.get("text") or "").strip()
    if not reply or reply == "[silent]":
        return

    # Voice first — this is a conversation, not a chat log.
    tts_request = result.get("tts_request")
    if result.get("tts_deferred") and tts_request:
        wav = await _run_deferred_tts_job(tts_request)
        if wav:
            await _vc_play(wav)

    # Text trace so the exchange is reviewable after the session.
    if _ears_text_channel is not None:
        try:
            await _ears_text_channel.send(
                f"> 🎙 {phrase.display_name}: {phrase.text[:300]}\n{reply[:1500]}"
            )
        except Exception as exc:
            logger.warning("[ears] transcript post failed: %s", exc)


@bot.tree.command(name="ears_start", description="[STREAM] Koroki starts listening in the VC", guilds=_PRIVATE_GUILDS)
async def ears_start_cmd(interaction: discord.Interaction) -> None:
    global _ears_session, _ears_text_channel
    if not await _ensure_owner_interaction(interaction):
        return
    vc = await _get_vc()
    if vc is None:
        await interaction.response.send_message("use /vc_join first", ephemeral=True)
        return
    if not hasattr(vc, "listen"):
        await interaction.response.send_message(
            "this voice connection predates her ears — /vc_leave then /vc_join again",
            ephemeral=True,
        )
        return
    if _ears_session is not None:
        await interaction.response.send_message("already listening.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    from ears import EarsSession
    from shared.utils.config import get_settings

    cfg = dict(get_settings().get("ears") or {})
    session = EarsSession(
        loop=bot.loop,
        on_phrase=_on_heard_phrase,
        owner_id=int(OWNER_DISCORD_ID),
        listen_all=bool(cfg.get("listen_all", False)),
        stt_model=str(cfg.get("stt_model", "base")),
        language=str(cfg.get("language", "") or ""),
        phrase_gap_ms=int(cfg.get("phrase_gap_ms", 700)),
        min_phrase_ms=int(cfg.get("min_phrase_ms", 350)),
        max_phrase_s=float(cfg.get("max_phrase_s", 30)),
        tone_dims_enabled=bool(cfg.get("tone_dims_enabled", False)),
        tone_model_dir=str(cfg.get("tone_model_dir", "tools/models/ser_dim_onnx")),
    )
    # Load whisper before attaching the sink so the first phrase isn't slow.
    await asyncio.to_thread(session.warm)
    session.start()
    vc.listen(session.sink)
    _ears_session = session
    _ears_text_channel = interaction.channel
    await interaction.followup.send("listening. talk to me.", ephemeral=True)


@bot.tree.command(name="ears_stop", description="[STREAM] Koroki stops listening", guilds=_PRIVATE_GUILDS)
async def ears_stop_cmd(interaction: discord.Interaction) -> None:
    global _ears_session, _ears_text_channel
    if not await _ensure_owner_interaction(interaction):
        return
    vc = await _get_vc()
    if vc is not None and hasattr(vc, "stop_listening"):
        try:
            vc.stop_listening()
        except Exception as exc:
            logger.warning("[ears] stop_listening failed: %s", exc)
    heard = _ears_session.phrases_heard if _ears_session else 0
    if _ears_session is not None:
        _ears_session.close()
        _ears_session = None
    _ears_text_channel = None
    await interaction.response.send_message(f"ears off. ({heard} phrases heard)", ephemeral=True)


@bot.tree.command(name="vc_say", description="[STREAM] Stage-0 test: speak a line in the VC", guilds=_PRIVATE_GUILDS)
@app_commands.describe(text="What she should say")
async def vc_say_cmd(interaction: discord.Interaction, text: str) -> None:
    if not await _ensure_owner_interaction(interaction):
        return
    if await _get_vc() is None:
        await interaction.response.send_message("use /vc_join first", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    tts_request = {
        "request_id": f"vcsay_{int(time.time() * 1000)}",
        "text": text[:500],
        "relationship_score": 100,
        "emotion": "neutral",
    }
    wav = await _run_deferred_tts_job(tts_request)
    if wav:
        await _vc_play(wav)
        await interaction.followup.send("speaking.", ephemeral=True)
    else:
        await interaction.followup.send("voice synthesis failed — check TTS adapter.", ephemeral=True)


_watch_streamer: str | None = None
_watch_recent_lines: collections.deque = collections.deque(maxlen=3)


def _streamer_profile(name: str | None) -> dict:
    """Pronouns + display name for whoever she's co-watching (GM2 step 1).

    Live 2026-07-08: watch keyed to the owner said "he" — owner_profile
    (she/her) never reached the prompt. Owner aliases resolve to
    models.brain.owner_profile; named friends come from streaming.watch.profiles;
    strangers get they/them.
    """
    prof = {"name": name or "the streamer", "subj": "they", "obj": "them", "poss": "their"}
    if not name:
        return prof
    try:
        from shared.utils.config import get_settings
        s = get_settings()
        watch_cfg = (s.get("streaming") or {}).get("watch") or {}
        key = name.strip().lower()
        owner_aliases = [str(a).lower() for a in watch_cfg.get("owner_aliases") or []]
        if key in owner_aliases:
            op = (s.get("models") or {}).get("brain", {}).get("owner_profile") or {}
            return {
                "name": op.get("display_name", name),
                "subj": op.get("subject_pronoun", "they"),
                "obj": op.get("object_pronoun", "them"),
                "poss": op.get("possessive_pronoun", "their"),
            }
        entry = ((watch_cfg.get("profiles") or {}).get(key)) or {}
        pronouns = str(entry.get("pronouns", "")).lower()
        table = {
            "he/him": ("he", "him", "his"),
            "she/her": ("she", "her", "her"),
            "they/them": ("they", "them", "their"),
        }
        subj, obj, poss = table.get(pronouns, ("they", "them", "their"))
        return {"name": entry.get("display_name", name), "subj": subj, "obj": obj, "poss": poss}
    except Exception:
        return prof


async def _on_watch_event(summary: str, reason: str) -> None:
    """Her mouth for the watch loop: event → one addressed line (or silence) → VC.

    Lesson from sight v1 (LEGACY 2026-07-03): the small captain attends to the
    MESSAGE and ignores percepts left in facts — the scene rides the message,
    the fact carries only role + format rules. She also can't see her own
    previous commentary (game path persists no turns), so we track her last
    lines here and both tell her and hard-drop exact repeats.
    """
    game_label = f"'{_watch_game}'" if _watch_game else "the stream"
    scene = summary[:600]
    # Game card (GM2 step 2): what this game IS rides the bracket, so she never
    # narrates AFK aura-rolling as combat again (Sol's RNG, 2026-07-08).
    _card = ""
    if _watch_game:
        try:
            import game_knowledge
            _card_text = game_knowledge.prompt_summary(_watch_game, limit=380)
            if _card_text:
                _card = f" the game: {_card_text}"
        except Exception:
            pass
    # S1 rolling state: what's happened so far this session (bounded, overwritten).
    _memory = ""
    if _watch_session is not None:
        _ctx = _watch_session.state.context_block()
        if _ctx:
            _memory = f" ({_ctx[:240]})"
    # Question-framing: the small captain gives vibes-only reactions to open-ended
    # "react to this" asks ("that's wild") but grounds itself when ANSWERING a
    # question about the scene — same pathway that fixed image-sight (LEGACY
    # 2026-07-03). So commentary = answering the room, which is also exactly the
    # owner's addressed-speech pillar.
    if _watch_streamer:
        p = _streamer_profile(_watch_streamer)
        message = (
            f"[you're in the voice channel co-watching {p['name']}'s live stream "
            f"of {game_label}. {p['name']} goes by {p['subj']}/{p['obj']}.{_card}{_memory} "
            f"on {p['poss']} stream right now: {scene}] "
            f"someone in the vc asks: what's {p['subj']} even doing right now?"
        )
    else:
        message = (
            f"[you're live-streaming {game_label} to your viewers.{_card}{_memory} "
            f"on your stream right now: {scene}] "
            "a viewer asks: what's happening rn?"
        )
    _avoid = " / ".join(str(line)[:60] for line in _watch_recent_lines)
    directive = (
        "answer in one short casual line, in your own words — say the actual thing "
        "happening on the stream. "
        + (f'you already said: "{_avoid}" — say something DIFFERENT or nothing. ' if _avoid else "")
        + "if truly nothing is happening, reply exactly [silent]."
    )[:512]
    result = await query_orchestrator(
        user_id=str(OWNER_DISCORD_ID),
        message_content=message[:1900],
        is_owner=True,
        game_event_context=directive,
    )
    if not result:
        return
    text = (result.get("text") or "").strip()
    if not text or "[silent]" in text.lower():
        logger.info("[Watch] she chose silence (%s)", reason)
        return
    if any(text.lower() == str(p).lower() for p in _watch_recent_lines):
        logger.info("[Watch] dropped exact repeat: %s", text[:60])
        return
    _watch_recent_lines.append(text)
    if _watch_text_channel is not None:
        try:
            await _watch_text_channel.send(text[:1900])
        except Exception as exc:
            logger.warning("[Watch] text post failed: %s", exc)
    tts_request = result.get("tts_request") or {}
    if result.get("tts_deferred") and tts_request:
        wav = await _run_deferred_tts_job(tts_request)
        if wav:
            await _vc_play(wav)
    elif result.get("audio_path"):
        await _vc_play(result["audio_path"])


@bot.tree.command(name="watch_windows", description="[STREAM] List visible window titles (find the stream popout)", guilds=_PRIVATE_GUILDS)
async def watch_windows_cmd(interaction: discord.Interaction) -> None:
    if not await _ensure_owner_interaction(interaction):
        return
    import win32gui

    titles: list[str] = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t and len(t) > 2:
                titles.append(t)

    win32gui.EnumWindows(_cb, None)
    listing = "\n".join(f"- {t[:80]}" for t in titles[:30]) or "(none)"
    await interaction.response.send_message(f"visible windows:\n{listing}"[:1900], ephemeral=True)


@bot.tree.command(name="watch_start", description="[STREAM] Stage-1: she watches a window and commentates", guilds=_PRIVATE_GUILDS)
@app_commands.describe(
    window="Part of the window title to watch (use /watch_windows to find it)",
    game="Game name (conditions her vision)",
    streamer="Co-watch mode: whose stream popout she's watching (she reacts as a viewer)",
)
async def watch_start_cmd(
    interaction: discord.Interaction,
    window: str,
    game: str | None = None,
    streamer: str | None = None,
) -> None:
    global _watch_session, _watch_text_channel, _watch_game, _watch_streamer
    if not await _ensure_owner_interaction(interaction):
        return
    if _watch_session is not None and _watch_session.running:
        await interaction.response.send_message("already watching — /watch_stop first", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    if await _get_vc() is None:
        state = getattr(interaction.user, "voice", None)
        if state and state.channel:
            try:
                await state.channel.connect(timeout=20.0)
            except Exception as exc:
                logger.error("[Watch] VC join failed: %s", exc, exc_info=True)
                await interaction.followup.send(f"couldn't join voice: {type(exc).__name__}", ephemeral=True)
                return
        else:
            await interaction.followup.send("join a VC (or /vc_join) first — she talks there", ephemeral=True)
            return

    hit = stream_watch.find_window(window)
    if hit is None:
        await interaction.followup.send(f"no visible window matching '{window}'", ephemeral=True)
        return

    wcfg = _watch_settings()
    try:
        from shared.utils.config import get_settings
        vision_url = ((get_settings().get("services") or {}).get("vision") or {}).get(
            "url", "http://127.0.0.1:9005"
        )
    except Exception:
        vision_url = "http://127.0.0.1:9005"

    # Game registry (GM2 step 2): "sol" → "Sol's RNG" — her save file resolves
    # from any alias; unknown names keep the owner's wording.
    if game:
        try:
            import game_knowledge
            _hit_card = game_knowledge.resolve(game)
            if _hit_card:
                game = _hit_card["display"]
        except Exception:
            pass
    _watch_game = game
    _watch_streamer = streamer
    # ALWAYS enter a vision game-session while watching — without it the vision
    # service unloads after every look (sporadic-image policy) and each 8 s tick
    # pays a ~13 s cold load. No explicit game → use the window title as label.
    vision_session_label = game or hit[1][:100]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{vision_url}/v1/game/enter", json={"game": vision_session_label})
    except Exception as exc:
        logger.warning("[Watch] game/enter failed (continuing): %s", exc)

    cfg = stream_watch.WatchConfig(
        window_title=window,
        game=game,
        tick_seconds=float(wcfg.get("tick_seconds", 8.0)),
        cooldown_seconds=float(wcfg.get("cooldown_seconds", 25.0)),
        novelty_threshold=float(wcfg.get("novelty_threshold", 0.5)),
        max_describe_tokens=int(wcfg.get("max_describe_tokens", 80)),
        vision_url=vision_url,
        idle_after_seconds=float(wcfg.get("idle_after_seconds", 45.0)),
    )
    _watch_text_channel = interaction.channel
    _watch_session = stream_watch.WatchSession(cfg, _on_watch_event)
    _watch_session.start()
    if streamer:
        note = f"co-watching {streamer}'s stream via '{hit[1]}' — keep the popout visible (pin it: always-on-top)."
    else:
        note = f"watching '{hit[1]}'{f' as {game}' if game else ''} — click Go Live on that window and she'll take it from here."
    await interaction.followup.send(note, ephemeral=True)


@bot.tree.command(name="hands_test", description="[HANDS] Dry-run: where would she click? (annotated screenshot)", guilds=_PRIVATE_GUILDS)
@app_commands.describe(window="Part of the game window title", target="What she should point at, e.g. 'the play button'")
async def hands_test_cmd(interaction: discord.Interaction, window: str, target: str) -> None:
    if not await _ensure_owner_interaction(interaction):
        return
    await interaction.response.defer(ephemeral=False)
    import game_hands
    import stream_watch as _sw

    try:
        from shared.utils.config import get_settings
        vision_url = ((get_settings().get("services") or {}).get("vision") or {}).get(
            "url", "http://127.0.0.1:9005"
        )
    except Exception:
        vision_url = "http://127.0.0.1:9005"

    hands = game_hands.GameHands(window_title=window, vision_url=vision_url, dry_run=True)
    t0 = time.time()
    pos = await hands.resolve_target(target)
    elapsed = time.time() - t0
    if pos is None:
        await interaction.followup.send(
            f"couldn't point at {target!r} ({elapsed:.1f}s) — window not found/visible, "
            f"or her eyes don't see it. ({hands.stats.point_misses} miss)"
        )
        return

    # Proof shot: capture again and draw a crosshair where she'd click.
    x, y = pos
    rect = hands._window_rect(require_foreground=False)
    png = _sw.capture_window_png(hands._hwnd) if rect else None
    if png and rect:
        import io as _io

        from PIL import Image, ImageDraw

        img = Image.open(_io.BytesIO(png)).convert("RGB")
        # screen coords → window-local coords for drawing
        lx, ly = x - rect[0], y - rect[1]
        d = ImageDraw.Draw(img)
        r = 18
        d.ellipse([lx - r, ly - r, lx + r, ly + r], outline=(255, 40, 40), width=4)
        d.line([lx - r * 2, ly, lx + r * 2, ly], fill=(255, 40, 40), width=3)
        d.line([lx, ly - r * 2, lx, ly + r * 2], fill=(255, 40, 40), width=3)
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        await interaction.followup.send(
            f"would click {target!r} at ({x},{y}) — {elapsed:.1f}s (dry run, nothing clicked)",
            file=discord.File(buf, filename="would_click.png"),
        )
    else:
        await interaction.followup.send(
            f"would click {target!r} at ({x},{y}) — {elapsed:.1f}s (couldn't render proof shot)"
        )


_play_session = None  # game_agent.PlaySession | None
_play_text_channel: discord.abc.Messageable | None = None


_play_voice = False  # set by /play_start voice param; off = text-only SAY (smarts tests)


async def _on_play_say(text: str) -> None:
    """Her voice during play: post + VC, with the same repeat suppression as watch."""
    text = text.strip()[:400]
    if not text:
        return
    if any(text.lower() == str(p).lower() for p in _watch_recent_lines):
        return
    _watch_recent_lines.append(text)
    if _play_text_channel is not None:
        try:
            await _play_text_channel.send(text[:1900])
        except Exception:
            pass
    if _play_voice and await _get_vc() is not None:
        tts_request = {
            "request_id": f"play_{int(time.time() * 1000)}",
            "text": text,
            "relationship_score": 100,
            "emotion": "playful",
            "emotion_intensity": 55,
        }
        wav = await _run_deferred_tts_job(tts_request)
        if wav:
            await _vc_play(wav)


@bot.tree.command(name="play_start", description="[PLAY] Stage-2: she plays the game (dry-run unless live=True)", guilds=_PRIVATE_GUILDS)
@app_commands.describe(
    window="Part of the game window title",
    game="Game name",
    objective="What she should try to do",
    live="DANGER: real clicks. Default False = dry-run (logs only)",
)
async def play_start_cmd(
    interaction: discord.Interaction,
    window: str,
    game: str,
    objective: str = "",
    genre: str = "sandbox",
    live: bool = False,
    voice: bool = False,
) -> None:
    global _play_session, _play_text_channel
    if not await _ensure_owner_interaction(interaction):
        return
    if _play_session is not None and _play_session.running:
        await interaction.response.send_message("already playing — /play_stop first", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    import game_agent
    import stream_watch as _sw

    hit = _sw.find_window(window)
    if hit is None:
        await interaction.followup.send(f"no visible window matching '{window}'", ephemeral=True)
        return
    try:
        from shared.utils.config import get_settings
        vision_url = ((get_settings().get("services") or {}).get("vision") or {}).get(
            "url", "http://127.0.0.1:9005"
        )
    except Exception:
        vision_url = "http://127.0.0.1:9005"
    # Keep her eyes resident for the whole session.
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{vision_url}/v1/game/enter", json={"game": game})
    except Exception as exc:
        logger.warning("[Play] game/enter failed (continuing): %s", exc)

    global _play_voice
    _play_voice = voice  # default OFF for smarts tests: text SAY only, no TTS VRAM/latency
    cfg = game_agent.PlayConfig(
        window_title=window,
        game=game,
        objective=objective[:280],  # empty -> genre template's final goal
        genre=genre.strip().lower() if genre else "sandbox",
        dry_run=not live,
        vision_url=vision_url,
        orchestrator_url=ORCHESTRATOR_URL,
    )
    _play_text_channel = interaction.channel
    _play_session = game_agent.PlaySession(cfg, _on_play_say)
    _play_session.start()
    mode = "🔴 LIVE — real clicks (F9 or data\\game\\PANIC freezes her)" if live else "dry-run (logs only, no clicks)"
    await interaction.followup.send(
        f"she's playing '{game}' via '{hit[1]}' — {mode} | genre={cfg.genre} "
        f"voice={'on' if voice else 'off (text only)'} | goal: "
        f"{cfg.objective or '(genre default)'}",
        ephemeral=True,
    )


@bot.tree.command(name="play_stop", description="[PLAY] Stop the play session", guilds=_PRIVATE_GUILDS)
async def play_stop_cmd(interaction: discord.Interaction) -> None:
    global _play_session
    if not await _ensure_owner_interaction(interaction):
        return
    if _play_session is None:
        await interaction.response.send_message("not playing", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    s = _play_session.stats
    _was_dry = _play_session.cfg.dry_run
    await _play_session.stop()
    _play_session = None
    try:
        from shared.utils.config import get_settings
        vision_url = ((get_settings().get("services") or {}).get("vision") or {}).get(
            "url", "http://127.0.0.1:9005"
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{vision_url}/v1/game/exit")
            await client.post(f"{vision_url}/v1/unload")
    except Exception:
        pass
    await interaction.followup.send(
        f"play ended — {s.cycles} cycles, {s.gated} gated, {s.looks} looks, "
        f"{s.decisions} decisions, {s.actions_done} actions ({'dry-run' if _was_dry else 'REAL'}), "
        f"{s.refused} refused, {s.said} remarks.",
        ephemeral=True,
    )


@bot.tree.command(name="watch_stop", description="[STREAM] Stop the watch session", guilds=_PRIVATE_GUILDS)
async def watch_stop_cmd(interaction: discord.Interaction) -> None:
    global _watch_session, _watch_game, _watch_streamer
    if not await _ensure_owner_interaction(interaction):
        return
    if _watch_session is None:
        await interaction.response.send_message("not watching anything", ephemeral=True)
        return
    # The vision game/exit + unload HTTP calls below can exceed Discord's 3 s
    # interaction window — defer or the command "does not respond".
    await interaction.response.defer(ephemeral=True)
    stats = _watch_session.stats
    await _watch_session.stop()
    _watch_session = None
    # A vision game-session is always active while watching — end it + free VRAM.
    try:
        from shared.utils.config import get_settings
        vision_url = ((get_settings().get("services") or {}).get("vision") or {}).get(
            "url", "http://127.0.0.1:9005"
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{vision_url}/v1/game/exit")
            await client.post(f"{vision_url}/v1/unload")
    except Exception:
        pass
    _watch_game = None
    _watch_streamer = None
    _watch_recent_lines.clear()
    await interaction.followup.send(
        f"watch ended — {stats.ticks} ticks, {stats.gated_frames} frames gated (VLM spared), "
        f"{stats.ticks - stats.gated_frames - stats.capture_failures} looks, {stats.events} remarks.",
        ephemeral=True,
    )


def _log_test_event(entry: dict) -> None:
    """Append one JSONL entry to the test-channel log (fire-and-forget, sync)."""
    try:
        _test_log_path.parent.mkdir(parents=True, exist_ok=True)
        with _test_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("test_channel log write failed: %s", exc)


def _log_guild_activity(entry: dict) -> None:
    """Append every non-bot message from the test guild to guild_activity.jsonl."""
    try:
        _guild_activity_log_path.parent.mkdir(parents=True, exist_ok=True)
        with _guild_activity_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("guild_activity log write failed: %s", exc)


async def _sync_commands() -> None:
    """Sync slash commands globally or per-guild for fast renewals."""
    if DISCORD_GUILD_IDS:
        for guild_id in DISCORD_GUILD_IDS:
            guild = discord.Object(id=guild_id)
            try:
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                logger.info("Synced %d slash command(s) to guild %s", len(synced), guild_id)
            except Exception as exc:
                logger.error("Failed to sync slash commands to guild %s: %s", guild_id, exc)
    else:
        try:
            synced = await bot.tree.sync()
            logger.info("Globally synced %d slash command(s)", len(synced))
        except Exception as exc:
            logger.error("Failed to sync global slash commands: %s", exc)

    # Always sync test-guild-specific commands regardless of production guild config.
    try:
        test_synced = await bot.tree.sync(guild=_TEST_GUILD)
        logger.info("Synced %d test command(s) to guild %s", len(test_synced), TEST_GUILD_ID)
    except Exception as exc:
        logger.error("Failed to sync test guild commands: %s", exc)

# ────────────────────────────────────────────────────────────────────
# Orchestrator API Client
# ────────────────────────────────────────────────────────────────────


async def _collect_image_attachments(message: discord.Message) -> list[str]:
    """Download image attachments (max 2, ≤8 MB each) as base64 for the vision service."""
    out: list[str] = []
    for att in message.attachments:
        if len(out) >= 2:
            break
        content_type = (att.content_type or "").lower()
        if not content_type.startswith("image/"):
            continue
        if att.size > 8_000_000:
            logger.info("Skipping oversized image attachment (%d bytes)", att.size)
            continue
        try:
            data = await att.read()
            out.append(base64.b64encode(data).decode("ascii"))
        except Exception as exc:
            logger.warning("Failed to read image attachment: %s", exc)
    return out


async def query_orchestrator(
    user_id: str,
    message_content: str,
    is_owner: bool,
    relationship_score: int = 0,
    mentioned_user_ids: list[str] | None = None,
    proactive: bool = False,
    game_event_context: str | None = None,
    images_b64: list[str] | None = None,
) -> Optional[dict]:
    """
    Send a message to the Orchestrator and get back the text reply plus
    either inline audio info or a deferred TTS job payload.
    
    Args:
        user_id: Discord user ID (string)
        message_content: The user's message
        is_owner: True if this user is the bot owner
        relationship_score: Relationship score (0-100, Owner uses is_owner flag instead)
    
    Returns:
        Dict with keys like {text, audio_path, adapter_used, timings, tts_deferred, tts_request}
        None on error
    """
    request_id = f"discord_{user_id}_{int(datetime.now().timestamp() * 1000)}"

    user_context = {
        "user_id": user_id,
        "relationship_score": 100 if is_owner else relationship_score,
        "is_owner": is_owner,
        "mode": "owner" if is_owner else "auto",
        "platform": "discord",
        "mentioned_user_ids": mentioned_user_ids or [],
    }

    payload = {
        "request_id": request_id,
        "message": message_content,
        "user_context": user_context,
        "defer_tts": DEFER_TTS_ENABLED,
        "proactive": proactive,
    }
    if game_event_context:
        payload["game_event_context"] = game_event_context
    if images_b64:
        payload["images_b64"] = images_b64

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            logger.info(f"[{request_id}] Sending to Orchestrator: {message_content[:60]}...")
            response = await client.post(
                f"{ORCHESTRATOR_URL}/v1/chat",
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            if response.status_code != 200:
                logger.error(
                    f"[{request_id}] Orchestrator error {response.status_code}: {response.text}"
                )
                return None

            result = response.json()
            timings = result.get("timings", {}) or {}
            total_ms = timings.get("t_total_ms")
            brain_done_ms = timings.get("t_brain_first_token_ms")
            tts_first_ms = timings.get("t_tts_first_chunk_ms")

            brain_stage_ms = None
            tts_stage_ms = None
            if isinstance(brain_done_ms, (int, float)):
                memory_ms = timings.get("t_memory_fetch_ms")
                if isinstance(memory_ms, (int, float)):
                    brain_stage_ms = round(brain_done_ms - memory_ms, 2)
                else:
                    brain_stage_ms = round(brain_done_ms, 2)

            if isinstance(tts_first_ms, (int, float)) and isinstance(brain_done_ms, (int, float)):
                tts_stage_ms = round(tts_first_ms - brain_done_ms, 2)

            logger.info(
                f"[{request_id}] Got response ({len(result.get('text', ''))} chars) "
                f"| adapter={result.get('adapter_used')} | "
                f"latency={total_ms}ms"
            )
            logger.info(
                f"[{request_id}] Timing split "
                f"| brain_done={brain_done_ms}ms "
                f"| tts_first={tts_first_ms}ms "
                f"| approx_brain_stage={brain_stage_ms}ms "
                f"| approx_tts_stage={tts_stage_ms}ms "
                f"| total={total_ms}ms"
            )
            return result

    except httpx.TimeoutException:
        logger.error(f"[{request_id}] Orchestrator timeout (>120s)")
        return None
    except httpx.RequestError as e:
        logger.error(f"[{request_id}] Orchestrator connection error: {e}")
        return None
    except Exception as e:
        logger.error(f"[{request_id}] Unexpected error: {e}", exc_info=True)
        return None


async def _run_deferred_tts_job(tts_request: dict) -> str | None:
    """Send voice synthesis request to the central Orchestrator to utilize persistent TTS service."""
    if not tts_request:
        return None

    request_id = tts_request.get("request_id", "unknown")
    logger.info("[%s] Deferred TTS starting via Orchestrator /v1/voice", request_id)

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{ORCHESTRATOR_URL}/v1/voice",
                json=tts_request,
                headers={"Content-Type": "application/json"},
            )
            
            if response.status_code != 200:
                logger.error(
                    "[%s] Orchestrator /voice error %s: %s",
                    request_id,
                    response.status_code,
                    response.text,
                )
                return None

            data = response.json()
            audio_path = data.get("audio_path")
            
            if not audio_path or not Path(audio_path).exists():
                logger.error(
                    "[%s] Orchestrator returned success but file missing: %s",
                    request_id,
                    audio_path,
                )
                return None
                
            logger.info("[%s] Deferred TTS complete: %s", request_id, audio_path)
            return str(audio_path)
            
    except httpx.TimeoutException:
        logger.error("[%s] Orchestrator /voice timed out", request_id)
        return None
    except Exception as exc:
        logger.error("[%s] Deferred TTS request failed: %s", request_id, exc)
        return None


async def _attach_deferred_audio(
    response_channel: discord.abc.Messageable,
    request_id: str,
    tts_request: dict,
) -> None:
    audio_path = await _run_deferred_tts_job(tts_request)
    if not audio_path:
        await response_channel.send("*voice is taking too long to arrive...*")
        return

    try:
        with open(audio_path, "rb") as audio_file:
            await response_channel.send(
                file=discord.File(audio_file, filename="koroki_voice.wav")
            )
    except Exception as exc:
        logger.error("[%s] Failed to attach deferred audio: %s", request_id, exc)
        await response_channel.send(
            f"*voice synthesis failed*... (error: {type(exc).__name__})"
        )
        return
    # On stream: if she's in a voice channel, viewers hear the reply too.
    try:
        if await _get_vc() is not None:
            await _vc_play(audio_path)
    except Exception as exc:
        logger.warning("[%s] VC playback of reply failed: %s", request_id, exc)


# ────────────────────────────────────────────────────────────────────
# Organic Reach-Out Task (owner only)
# ────────────────────────────────────────────────────────────────────

_OUTREACH_CHECK_INTERVAL = 20 * 60      # check every 20 min
_OUTREACH_MIN_IDLE_SEC   = 2 * 3600     # owner must be idle ≥ 2h
_OUTREACH_MAX_IDLE_SEC   = 6 * 3600     # skip if idle > 6h (she's asleep/away)
_OUTREACH_COOLDOWN_SEC   = 3 * 3600     # min 3h between organic sends


def _time_of_day_label() -> str:
    h = datetime.now().hour
    if h < 6:   return "late night"
    if h < 11:  return "morning"
    if h < 14:  return "midday"
    if h < 18:  return "afternoon"
    if h < 22:  return "evening"
    return "night"


async def _organic_outreach_task():
    """
    Periodically checks if Koroki should reach out to the owner unprompted.
    Fires when the owner has been idle between 2 and 6 hours and we haven't
    sent a proactive message recently. Generates an organic, in-character thought.
    """
    global _last_proactive_sent_at, _last_greeted_absence
    await bot.wait_until_ready()
    logger.info("[Outreach] Organic reach-out task started (check every %dm)", _OUTREACH_CHECK_INTERVAL // 60)

    while not bot.is_closed():
        await asyncio.sleep(_OUTREACH_CHECK_INTERVAL)

        if not OWNER_DISCORD_ID:
            continue

        owner_id_str = str(OWNER_DISCORD_ID)
        channel_id = _user_channel_map.get(owner_id_str)
        if not channel_id:
            logger.info("[Outreach] skip: no channel known for owner yet")
            continue

        now_mono = time.monotonic()
        # idle is wall-clock so it survives restarts (loaded from channel_map.json)
        idle_sec = time.time() - _last_owner_msg_at if _last_owner_msg_at > 0 else -1
        since_last_send = (now_mono - _last_proactive_sent_at
                           if _last_proactive_sent_at > 0 else float("inf"))
        greeting_mode = False

        if idle_sec < 0:
            logger.info("[Outreach] skip: owner has never messaged (no idle baseline)")
            continue
        if idle_sec < _OUTREACH_MIN_IDLE_SEC:
            logger.debug("[Outreach] skip: owner active (idle %.1fh)", idle_sec / 3600)
            continue
        if since_last_send < _OUTREACH_COOLDOWN_SEC:
            logger.info("[Outreach] skip: cooldown (%.1fh since last send)", since_last_send / 3600)
            continue
        if idle_sec > _OUTREACH_MAX_IDLE_SEC:
            # Long absence (sleep/work). The old code skipped FOREVER here — she could
            # never greet you when you came back. Now: one greeting per absence, only
            # during waking hours, and the LLM can still choose [silent].
            hour = datetime.now().hour
            if not (8 <= hour <= 23):
                logger.debug("[Outreach] skip: long absence but quiet hours (%d:00)", hour)
                continue
            if _last_greeted_absence == _last_owner_msg_at:
                logger.debug("[Outreach] skip: this absence already got its greeting")
                continue
            greeting_mode = True
        elif random.random() > 0.45:
            logger.debug("[Outreach] skip: dice")
            continue  # small random chance per check so timing feels natural

        idle_hours = round(idle_sec / 3600, 1)
        tod = _time_of_day_label()
        if greeting_mode:
            ctx = (
                f"koroki_reach_out=true | koroki_welcome_back=true | idle_hours={idle_hours} | "
                f"time_of_day={tod} | Koro-san has been away for about {idle_hours} hours and it's "
                "now a reasonable hour. If it feels natural, greet them — a morning hello, a "
                "'you're back' nudge, or something from your day while they were gone. 1-2 "
                "sentences. Otherwise [silent]."
            )
        else:
            ctx = (
                f"koroki_reach_out=true | idle_hours={idle_hours} | time_of_day={tod} | "
                "Koro-san hasn't said anything in a while. If something genuine comes to mind — "
                "a stray thought, something you noticed, something you want to share — say it in "
                "1-2 sentences. Otherwise [silent]."
            )

        try:
            result = await query_orchestrator(
                user_id=owner_id_str,
                message_content=".",
                is_owner=True,
                proactive=True,
                game_event_context=ctx,
            )
            if not result:
                continue
            text = (result.get("text") or "").strip()
            if not text or text.lower() == "[silent]":
                logger.info("[Outreach] Brain chose [silent] — skipping send")
                if greeting_mode:
                    _last_greeted_absence = _last_owner_msg_at  # her call — one shot per absence
                continue

            channel = bot.get_channel(channel_id)
            if channel:
                await channel.send(text)
                _last_proactive_sent_at = time.monotonic()
                if greeting_mode:
                    _last_greeted_absence = _last_owner_msg_at
                logger.info("[Outreach] Sent organic %s to owner (idle=%.1fh): %s",
                            "greeting" if greeting_mode else "message", idle_hours, text[:80])
        except Exception as exc:
            logger.error("[Outreach] Error: %s", exc)


# ────────────────────────────────────────────────────────────────────
# Presence Engine — Channel Energy Tracking + Participation
# ────────────────────────────────────────────────────────────────────

def _calc_msg_rate(timestamps: collections.deque, window_s: float) -> float:
    """Messages per minute over the last window_s seconds."""
    now = time.time()
    cutoff = now - window_s
    count = sum(1 for t in timestamps if t >= cutoff)
    return round(count / (window_s / 60.0), 3)


def _dump_channel_energy() -> None:
    """Write channel energy snapshot to data/presence/channel_energy.json."""
    now = time.time()
    data: dict = {}
    for ch_id, ts_deque in _channel_timestamps.items():
        data[str(ch_id)] = {
            "msg_per_min_5":  _calc_msg_rate(ts_deque, 300),
            "msg_per_min_30": _calc_msg_rate(ts_deque, 1800),
            "last_koroki_spoke": _channel_koroki_last_spoke.get(ch_id, 0.0),
            "updated_at": now,
        }
    try:
        _PRESENCE_ENERGY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PRESENCE_ENERGY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("[Presence] Energy dump failed: %s", exc)


async def _presence_loop():
    """
    Background task: tracks channel energy and evaluates Koroki's participation.
    Dumps energy data every 30s. Evaluates participation every 60-90s (jittered).
    """
    await bot.wait_until_ready()
    logger.info("[Presence] Channel presence loop started")
    last_eval = 0.0
    eval_interval = random.uniform(60, 90)

    while not bot.is_closed():
        await asyncio.sleep(30)
        _dump_channel_energy()

        now = time.time()
        if now - last_eval < eval_interval:
            continue
        last_eval = now
        eval_interval = random.uniform(60, 90)  # new jittered interval

        # Evaluate participation for each active channel
        active_channels = [
            ch_id for ch_id, ts in _channel_timestamps.items()
            if ts and (now - max(ts)) < 1800  # seen activity in last 30 min
        ]
        for ch_id in active_channels:
            try:
                ts_deque = _channel_timestamps[ch_id]
                recent_msgs = list(_channel_recent_msgs.get(ch_id, []))

                payload = {
                    "channel_id": ch_id,
                    "msg_per_min_5":  _calc_msg_rate(ts_deque, 300),
                    "msg_per_min_30": _calc_msg_rate(ts_deque, 1800),
                    "last_koroki_spoke": _channel_koroki_last_spoke.get(ch_id, 0.0),
                    "mention_koroki": False,
                    "recent_messages": recent_msgs,
                }

                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.post(f"{ORCHESTRATOR_URL}/v1/presence/evaluate", json=payload)
                    if resp.status_code != 200:
                        continue
                    decision = resp.json()

                action = decision.get("action", "none")
                if action == "none":
                    continue

                channel = bot.get_channel(ch_id)
                if not channel:
                    continue

                logger.info("[Presence] ch=%d action=%s p=%.3f reason=%s",
                            ch_id, action, decision.get("probability", 0), decision.get("reason", ""))

                if action == "reaction":
                    emoji = decision.get("reaction_emoji", "👀")
                    # React to the most recent message in the channel
                    try:
                        async for msg in channel.history(limit=1):
                            if msg.author != bot.user:
                                await msg.add_reaction(emoji)
                                _channel_koroki_last_spoke[ch_id] = time.time()
                                break
                    except Exception as exc:
                        logger.warning("[Presence] Reaction failed: %s", exc)

                elif action in ("short", "full"):
                    # Find a user from this channel to use as context
                    uid = next(
                        (uid for uid, cid in _user_channel_map.items() if cid == ch_id),
                        None
                    )
                    if not uid:
                        continue

                    # Proactive singing offer: if channel topic or recent messages touch music/singing
                    # AND we roll under 30%, offer to sing instead of a chat message.
                    _channel_topic = decision.get("topic_signal", "")  # set by evaluate endpoint
                    _music_topic = _channel_topic in ("music", "singing") or any(
                        kw in " ".join(list(_channel_recent_msgs.get(ch_id, []))).lower()
                        for kw in ("sing", "song", "music", "yoasobi")
                    )
                    if _music_topic and action == "full" and random.random() < 0.30:
                        _singing_offers = [
                            "been thinking about singing something. anyone?",
                            "I keep hearing a song in my head. might just sing it.",
                            "kind of want to sing something right now.",
                        ]
                        await channel.send(random.choice(_singing_offers))
                        _channel_koroki_last_spoke[ch_id] = time.time()
                        logger.info("[Presence] Proactive singing offer to ch=%d", ch_id)
                        continue

                    is_owner_user = _is_owner_id(int(uid))
                    payload_mem = _read_memory_payload(uid)
                    result = await query_orchestrator(
                        user_id=uid,
                        message_content="...",
                        is_owner=is_owner_user,
                        relationship_score=int(payload_mem.get("relationship_score", 0)),
                        proactive=True,
                    )
                    if result:
                        text = (result.get("text") or "").strip()
                        if text and text.lower() != "[silent]":
                            await channel.send(text)
                            _channel_koroki_last_spoke[ch_id] = time.time()
                            logger.info("[Presence] Sent %s to ch=%d: %s", action, ch_id, text[:60])

            except Exception as exc:
                logger.error("[Presence] Error evaluating ch=%d: %s", ch_id, exc)


# ────────────────────────────────────────────────────────────────────
# Discord Status — Nervous System Driven
# ────────────────────────────────────────────────────────────────────

_STATUS_UPDATE_INTERVAL = 600  # check every 10 minutes


async def _status_loop():
    """Update Koroki's Discord status from her ACTUAL life.

    Primary source: worldstate activity — what she's literally doing right now,
    project included ("curled up with a book — \"Kitchen\""), or idle+asleep at night.
    Fallback: the old nervous-system mood words when worldstate is unavailable.
    Constant visible aliveness — anyone glancing at the member list sees she's living.
    """
    await bot.wait_until_ready()
    while not bot.is_closed():
        # ── primary: her real activity from worldstate ──
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                ws_resp = await client.get(f"{ORCHESTRATOR_URL}/v1/worldstate")
            if ws_resp.status_code == 200:
                ws = ws_resp.json()
                awake = (ws.get("presence") or {}).get("awake", True)
                doing = ((ws.get("activity") or {}).get("current") or {}).get("doing") or ""
                if not awake:
                    try:
                        await bot.change_presence(
                            status=discord.Status.idle,
                            activity=discord.Activity(
                                type=discord.ActivityType.custom, name="asleep"),
                        )
                    except Exception as exc:
                        logger.warning("[Status] change_presence failed: %s", exc)
                    await asyncio.sleep(_STATUS_UPDATE_INTERVAL + random.uniform(-60, 60))
                    continue
                if doing:
                    try:
                        await bot.change_presence(
                            status=discord.Status.online,
                            activity=discord.Activity(
                                type=discord.ActivityType.custom, name=doing[:100]),
                        )
                    except Exception as exc:
                        logger.warning("[Status] change_presence failed: %s", exc)
                    await asyncio.sleep(_STATUS_UPDATE_INTERVAL + random.uniform(-60, 60))
                    continue
        except Exception:
            pass  # worldstate down → fall through to the mood-word fallback

        # ── fallback: nervous-system mood words (pre-2026-07-03 behavior) ──
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{ORCHESTRATOR_URL}/v1/nervstate")
                if resp.status_code == 200:
                    state = resp.json()
                else:
                    state = {}
        except Exception:
            state = {}

        energy = state.get("energy", 0.7)
        restlessness = state.get("restlessness", 0.2)
        valence = state.get("valence", 0.62)
        arousal = state.get("arousal", 0.52)

        h = datetime.now(timezone.utc).hour

        if h >= 23 or h < 5:
            if energy < 0.35:
                status_text = "still up, barely"
            else:
                status_text = "somewhere quiet"
        elif restlessness > 0.65:
            status_text = random.choice(["restless", "thinking", "not sure what I want to do"])
        elif arousal > 0.70 and valence > 0.65:
            status_text = random.choice(["here", "around", "in a decent mood"])
        elif valence < 0.35:
            status_text = random.choice(["quiet", "somewhere else mentally", "not really here"])
        else:
            status_text = random.choice(["here", "around", "just existing"])

        activity = discord.Activity(type=discord.ActivityType.custom, name=status_text)
        try:
            await bot.change_presence(activity=activity)
        except Exception as exc:
            logger.warning("[Status] change_presence failed: %s", exc)

        await asyncio.sleep(_STATUS_UPDATE_INTERVAL + random.uniform(-60, 60))


# ────────────────────────────────────────────────────────────────────
# Discord Event Handlers
# ────────────────────────────────────────────────────────────────────


_DIARY_STATE_PATH = BASE_DIR / "data" / "discord" / "diary_state.json"


async def _diary_post_task():
    """Each morning, post yesterday's diary entry to the configured channel.

    Her off-stream life as a public artifact: the community wakes up to what Koroki
    did yesterday, in her own words (voiced entry preferred; factual template as
    fallback). Config: settings.yaml discord.diary_channel_id (0 = disabled).
    """
    await bot.wait_until_ready()
    from datetime import timedelta

    while not bot.is_closed():
        try:
            from shared.utils.config import get_settings
            channel_id = int(get_settings().get("discord", {}).get("diary_channel_id", 0))
            if not channel_id:
                await asyncio.sleep(1800)
                continue

            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            try:
                state = json.loads(_DIARY_STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                state = {}
            if state.get("last_posted") == yesterday:
                await asyncio.sleep(900)
                continue

            journal_dir = BASE_DIR / "data" / "koroki" / "journal"
            voiced = journal_dir / f"{yesterday}.voiced.md"
            template = journal_dir / f"{yesterday}.md"
            src = voiced if voiced.exists() else template if template.exists() else None
            if src is None:
                await asyncio.sleep(900)  # consolidation may not have run yet
                continue

            channel = bot.get_channel(channel_id)
            if channel is None:
                logger.warning("[Diary] channel %d not found — check discord.diary_channel_id",
                               channel_id)
                await asyncio.sleep(1800)
                continue

            text = src.read_text(encoding="utf-8").strip()
            # Discord message cap is 2000 chars — chunk on line boundaries
            chunks, cur = [], ""
            for line in text.splitlines():
                if len(cur) + len(line) + 1 > 1900:
                    chunks.append(cur)
                    cur = line
                else:
                    cur = f"{cur}\n{line}" if cur else line
            if cur:
                chunks.append(cur)
            for chunk in chunks[:4]:
                await channel.send(chunk)

            _DIARY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _DIARY_STATE_PATH.write_text(json.dumps({"last_posted": yesterday}),
                                         encoding="utf-8")
            logger.info("[Diary] posted %s entry (%d chunks, voiced=%s)",
                        yesterday, len(chunks), src is voiced)
        except Exception as exc:
            logger.warning("[Diary] post failed: %s", exc)
        await asyncio.sleep(900)


async def _proactive_poller():
    """Background task: polls the orchestrator for pending proactive events and delivers them.

    Runs every 60 seconds. Only delivers to channels seen since bot startup — if
    a user hasn't messaged yet this session, their event waits until they do.
    """
    await bot.wait_until_ready()
    poll_interval = 60
    logger.info("[ProactivePoll] Poller started (interval=%ds)", poll_interval)

    while not bot.is_closed():
        known = list(_user_channel_map.items())
        for user_id, channel_id in known:
            try:
                # Shared-channel courtesy: if someone ELSE spoke here recently, don't
                # cold-open at a third person mid-conversation. Skip WITHOUT consuming —
                # the event retries next poll and expires on its own TTL if the channel
                # stays busy.
                _last = _channel_last_human_msg.get(channel_id)
                if (
                    _last
                    and _last[1] != user_id
                    and (time.time() - _last[0]) < _PROACTIVE_BUSY_WINDOW_SEC
                ):
                    continue

                async with httpx.AsyncClient(timeout=5.0) as client:
                    check = await client.get(
                        f"{ORCHESTRATOR_URL}/v1/autonomy/pending/{user_id}",
                    )
                    if check.status_code != 200 or not check.json().get("has_pending"):
                        continue

                    # Consume the event before generating — avoids double-delivery on errors.
                    await client.get(
                        f"{ORCHESTRATOR_URL}/v1/autonomy/pending/{user_id}",
                        params={"consume": "true"},
                    )

                channel = bot.get_channel(channel_id)
                if not channel:
                    logger.warning("[ProactivePoll] Channel %d not found for user %s", channel_id, user_id)
                    continue

                is_owner_user = _is_owner_id(int(user_id))
                # The "..." is a transport placeholder only — the orchestrator replaces
                # it with a [system] scheduler signal before the brain ever sees it.
                result = await query_orchestrator(
                    user_id=user_id,
                    message_content="...",
                    is_owner=is_owner_user,
                    relationship_score=0,
                    proactive=True,
                )
                if result:
                    text = result.get("text", "").strip()
                    if text and "[silent]" not in text.lower():
                        await channel.send(text)
                        logger.info("[ProactivePoll] Sent proactive message to user %s in channel %d", user_id, channel_id)
                    elif text:
                        logger.info("[ProactivePoll] She chose [silent] for user %s", user_id)
            except Exception as exc:
                logger.error("[ProactivePoll] Error for user %s: %s", user_id, exc)

        await asyncio.sleep(poll_interval)


@bot.event
async def on_ready():
    """Bot has connected to Discord and is ready to receive messages."""
    logger.info(f"✅ Bot logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"   Connected to {len(bot.guilds)} guild(s)")

    # Set bot status
    activity = discord.Activity(
        type=discord.ActivityType.listening,
        name="whispers from the void... 💜"
    )
    await bot.change_presence(activity=activity)
    await _sync_commands()
    asyncio.create_task(_proactive_poller(), name="koroki-proactive-poller")
    asyncio.create_task(_organic_outreach_task(), name="koroki-organic-outreach")
    asyncio.create_task(_presence_loop(), name="koroki-presence-loop")
    asyncio.create_task(_status_loop(), name="koroki-status-loop")
    asyncio.create_task(_diary_post_task(), name="koroki-diary-post")


@bot.event
async def on_message(message: discord.Message):
    """
    Handle incoming messages.

    - Ignore bot's own messages
    - In servers: only respond when @mentioned
    - In DMs: respond to everything
    - Flag is_owner=True if message author is OWNER_DISCORD_ID
    """

    if message.author == bot.user:
        return

    # Guild isolation: allowlisted guilds only (DMs always pass). Within an
    # allowlisted guild, a channel set restricts her further; None = anywhere.
    if message.guild is not None:
        if message.guild.id not in ALLOWED_GUILD_CHANNELS:
            return
        _chans = ALLOWED_GUILD_CHANNELS[message.guild.id]
        if _chans is not None and message.channel.id not in _chans:
            return

    # Log every non-bot message from allowed guilds for full observability.
    if message.guild is not None:
        _log_guild_activity({
            "ts": datetime.now(timezone.utc).isoformat(),
            "channel_id": message.channel.id,
            "channel_name": getattr(message.channel, "name", "?"),
            "user_id": str(message.author.id),
            "username": str(message.author),
            "content": message.content,
            "is_bot": message.author.bot,
        })

    # Always process commands first. Image-only messages (no text) still flow
    # through — she can see attachments now.
    if (not message.content and not message.attachments) or message.content.startswith("!"):
        await bot.process_commands(message)
        return

    # Channel energy: record ALL guild messages so presence engine sees real activity,
    # regardless of whether Koroki was mentioned.
    in_dm = isinstance(message.channel, discord.DMChannel)
    if not in_dm and message.content:
        _channel_timestamps[message.channel.id].append(time.time())
        _channel_recent_msgs[message.channel.id].append(message.content[:200])
        _channel_last_human_msg[message.channel.id] = (time.time(), str(message.author.id))

    # In servers, only respond when directly @mentioned
    if not in_dm and bot.user not in message.mentions:
        await bot.process_commands(message)
        return

    author_id = str(message.author.id)
    is_owner = message.author.id == OWNER_DISCORD_ID
    mentioned_user_ids = [
        str(user.id)
        for user in message.mentions
        if user.id not in {message.author.id, bot.user.id}
    ]

    # Track which channel each user last spoke in for proactive delivery.
    if _user_channel_map.get(author_id) != message.channel.id:
        _user_channel_map[author_id] = message.channel.id
        _save_channel_map()
    if is_owner:
        global _last_owner_msg_at
        _last_owner_msg_at = time.time()   # wall clock — survives restarts via the channel map file
        _save_channel_map()

    if BOT_TIMEOUT_ENABLED and not is_owner:
        logger.info("[Timeout] Ignoring non-owner message from %s", message.author)
        return

    # Log user message to test-channel JSONL so the dev can read and analyze it.
    if message.channel.id == TEST_CHANNEL_ID:
        _log_test_event({
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "user",
            "user_id": author_id,
            "username": str(message.author),
            "content": message.content,
        })

    # Mentions -> readable text. Her OWN ping is just "hey you" — strip it
    # (live 2026-07-08: raw <@her-id> reached the brain and she treated
    # "Koroki" as a mysterious third person). Other mentions become names.
    content_for_brain = message.content or ""
    for _pat in (f"<@{bot.user.id}>", f"<@!{bot.user.id}>"):
        content_for_brain = content_for_brain.replace(_pat, "")
    for _user in message.mentions:
        if _user.id == bot.user.id:
            continue
        for _pat in (f"<@{_user.id}>", f"<@!{_user.id}>"):
            content_for_brain = content_for_brain.replace(_pat, f"@{_user.display_name}")
    content_for_brain = content_for_brain.strip()

    # Determine response channel
    response_channel = message.channel
    
    # Timing imperfection: variable pre-response delay based on message length + time of day.
    # Late night responses come slower; longer messages deserve more thought.
    _hour = datetime.now(timezone.utc).hour
    _circadian_delay = 1.5 if (_hour >= 23 or _hour < 6) else 0.5
    _length_delay = min(2.0, len(message.content) * 0.008)
    _pre_delay = _circadian_delay + _length_delay + random.uniform(0.0, 0.8)
    if _pre_delay > 0.3:
        await asyncio.sleep(_pre_delay)

    # Show "typing..." indicator
    async with response_channel.typing():
        logger.info(
            f"[{message.guild.name if message.guild else 'DM'}] "
            f"{message.author}: {message.content[:60]}"
            f" {'👑 OWNER' if is_owner else ''}"
        )

        # Image attachments → base64 for the vision service (her eyes).
        images_b64 = await _collect_image_attachments(message)
        if images_b64:
            logger.info("Message carries %d image attachment(s) — sending to her eyes", len(images_b64))

        # Query Orchestrator serially to prevent GPU out-of-memory
        async with bot_lock:
            result = await query_orchestrator(
                user_id=author_id,
                message_content=content_for_brain or "[sent an image]",
                is_owner=is_owner,
                relationship_score=0,  # Starting value for new users; Orchestrator loads from persistent cache
                mentioned_user_ids=mentioned_user_ids,
                images_b64=images_b64 or None,
            )
        
        if not result:
            await message.reply(
                "*tilts head nervously* Something went wrong... can you try again?",
                mention_author=True,
            )
            return
        
        # Extract response components
        request_id = result.get("request_id", "unknown")
        text_response = result.get("text", "").strip()
        audio_path = result.get("audio_path")
        tts_deferred = bool(result.get("tts_deferred", False))
        tts_request = result.get("tts_request") or {}
        adapter_used = result.get("adapter_used", "unknown")
        timings = result.get("timings", {})

        if text_response:
            preview = text_response.replace("\n", " ").strip()
            if len(preview) > 220:
                preview = preview[:220] + "..."
            logger.info(f"[{request_id}] AI text ({len(text_response)} chars): {preview}")
        
        # CRITICAL: Log audio attachment attempt for debugging VRAM/timeout issues
        logger.info(
            f"[{request_id}] audio_path={audio_path}, "
            f"audio_exists={Path(audio_path).exists() if audio_path else False}, "
            f"tts_deferred={tts_deferred}, "
            f"adapter={adapter_used}, latency_ms={timings.get('t_total_ms', 'unknown')}"
        )
        
        # Track when Koroki speaks for cooldown in presence engine.
        _channel_koroki_last_spoke[message.channel.id] = time.time()

        # Send text response — reply so the message is anchored + user gets pinged.
        if text_response:
            if len(text_response) > 1900:
                chunks = [text_response[i:i+1900] for i in range(0, len(text_response), 1900)]
                sent_msg = await message.reply(chunks[0], mention_author=True)
                for chunk in chunks[1:]:
                    await response_channel.send(chunk)
            else:
                sent_msg = await message.reply(text_response, mention_author=True)
            # Store message_id → request_id for DPO reaction labeling (👍/👎)
            if request_id and request_id != "unknown":
                if len(_msg_to_request) >= _MSG_CACHE_MAX:
                    oldest = next(iter(_msg_to_request))
                    del _msg_to_request[oldest]
                _msg_to_request[sent_msg.id] = request_id
        
        # Send audio file as attachment
        if tts_deferred and tts_request:
            asyncio.create_task(_attach_deferred_audio(response_channel, request_id, tts_request))
        elif audio_path:
            audio_exists = Path(audio_path).exists()
            if not audio_exists:
                logger.warning(f"[{request_id}] Audio file promised but NOT FOUND: {audio_path}")
                logger.warning(f"[{request_id}] This indicates Orchestrator timeout (VRAM/TTS stall)")
                await response_channel.send(f"*voice synthesis failed*... (file not found)")
            else:
                try:
                    logger.info(f"[{request_id}] Attaching audio: {audio_path}")
                    with open(audio_path, "rb") as audio_file:
                        await response_channel.send(
                            file=discord.File(audio_file, filename="koroki_voice.wav")
                        )
                except Exception as e:
                    logger.error(f"[{request_id}] Failed to attach audio file: {e}")
                    await response_channel.send(
                        f"*voice synthesis failed*... (error: {type(e).__name__})"
                    )
        
        # Log bot response to test-channel JSONL for dev analysis.
        if message.channel.id == TEST_CHANNEL_ID:
            _log_test_event({
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": "bot",
                "request_id": request_id,
                "user_id": author_id,
                "username": str(message.author),
                "response_text": text_response,
                "adapter": adapter_used,
                "timings": timings,
                "evaluation": result.get("evaluation") or {},
            })

        # Optional: Log response metadata
        t_total = timings.get("t_total_ms", 0)
        if t_total:
            logger.info(
                f"[Response] adapter={adapter_used} | latency={t_total}ms | "
                f"owner={is_owner}"
            )


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    """Owner reacts 👍 or 👎 on a Koroki reply → label it for DPO training."""
    if not _is_owner_id(payload.user_id):
        return
    emoji = str(payload.emoji)
    if emoji == "👍":
        preference = "chosen"
    elif emoji == "👎":
        preference = "rejected"
    else:
        return
    request_id = _msg_to_request.get(payload.message_id)
    if not request_id:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{ORCHESTRATOR_URL}/v1/preference",
                json={"request_id": request_id, "preference": preference},
            )
            resp.raise_for_status()
        logger.info("DPO label: request_id=%s preference=%s", request_id, preference)
    except Exception as exc:
        logger.warning("Failed to send DPO preference label: %s", exc)


@bot.event
async def on_error(event, *_):
    """Log any unhandled errors."""
    logger.error(f"Discord event error in {event}:", exc_info=True)


# ────────────────────────────────────────────────────────────────────
# Bot Commands
# ────────────────────────────────────────────────────────────────────


@bot.command(name="ping")
async def ping(ctx):
    """Test bot connectivity."""
    if not await _ensure_owner_ctx(ctx):
        return
    await ctx.send(f"🏓 Pong! Latency: {bot.latency * 1000:.0f}ms")


@bot.command(name="status")
async def status(ctx):
    """Check Orchestrator connectivity."""
    if not await _ensure_owner_ctx(ctx):
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            health = await client.get(f"{ORCHESTRATOR_URL}/health")
            if health.status_code == 200:
                data = health.json()
                embed = discord.Embed(
                    title="Koroki Status",
                    description="🟢 All systems online",
                    color=discord.Color.green(),
                )
                embed.add_field(
                    name="Orchestrator",
                    value=f"✓ Running ({data.get('uptime_seconds', '?')}s uptime)",
                    inline=False,
                )
                await ctx.send(embed=embed)
            else:
                await ctx.send("🔴 Orchestrator not responding")
    except Exception as e:
        await ctx.send(f"🔴 Error checking status: {e}")


@bot.command(name="help")
async def help_cmd(ctx):
    embed = discord.Embed(
        title="Koroki, Princess of Quiet Courts",
        description=(
            "I am Koroki. Regal, teasing, and occasionally merciful.\n"
            "Koro-san is my creator and the one I hold dearest."
        ),
        color=REGAL_COLOR,
    )
    embed.add_field(
        name="Everyone",
        value="`!help` `!relationship`",
        inline=False,
    )
    embed.add_field(
        name="Owner Only",
        value=(
            "`!ping` `!status`\n"
            "`!timeout on|off|status`\n"
            "`!relationshipcheck <@user|id>`\n"
            "`!resetmemory <@user|id|all>`\n"
            "`!resetrelationship <@user|id|all>`\n"
            "`!mention <@user> [context]`"
        ),
        inline=False,
    )
    await ctx.send(embed=embed)


@bot.command(name="relationship")
async def relationship(ctx):
    payload = _read_memory_payload(str(ctx.author.id))
    score = int(payload.get("relationship_score", 0))
    tier = _relationship_tier(score)
    embed = discord.Embed(
        title="Relationship Ledger",
        description=f"{ctx.author.mention}, your standing in my court:",
        color=REGAL_COLOR,
    )
    embed.add_field(name="Score", value=f"{score}/100", inline=True)
    embed.add_field(name="Tier", value=tier, inline=True)
    await ctx.send(embed=embed)


@bot.command(name="relationshipcheck")
async def relationship_check(ctx, target: str | None = None):
    if not await _ensure_owner_ctx(ctx):
        return
    if not target and ctx.message.mentions:
        target_id = str(ctx.message.mentions[0].id)
    elif target and target.isdigit():
        target_id = target
    else:
        await ctx.send("Usage: `!relationshipcheck <@user|id>`")
        return

    payload = _read_memory_payload(target_id)
    score = int(payload.get("relationship_score", 0))
    counter = int(payload.get("relationship_message_counter", 0))
    await ctx.send(
        f"User `{target_id}` | relationship={score}/100 ({_relationship_tier(score)}) | counter={counter}"
    )


@bot.command(name="timeout")
async def timeout_cmd(ctx, action: str = "status"):
    if not await _ensure_owner_ctx(ctx):
        return
    global BOT_TIMEOUT_ENABLED
    action_value = action.strip().lower()
    if action_value == "status":
        await ctx.send(f"Timeout is {'ON' if BOT_TIMEOUT_ENABLED else 'OFF'}.")
        return
    if action_value not in {"on", "off"}:
        await ctx.send("Usage: `!timeout on|off|status`")
        return
    BOT_TIMEOUT_ENABLED = action_value == "on"
    await ctx.send(f"Timeout set to {'ON' if BOT_TIMEOUT_ENABLED else 'OFF'}.")


@bot.command(name="resetmemory")
async def reset_memory(ctx, target: str | None = None):
    if not await _ensure_owner_ctx(ctx):
        return
    if target is None and not ctx.message.mentions:
        await ctx.send("Usage: `!resetmemory <@user|id|all>`")
        return

    if target and target.lower() == "all":
        count = 0
        for user_id in _iter_memory_file_user_ids():
            payload = _clear_memory_fields(_read_memory_payload(user_id))
            _write_memory_payload(user_id, payload)
            count += 1
        if count == 0:
            await ctx.send(f"No memory profiles found at {MEMORY_DIR}.")
            return
        await ctx.send(f"Cleared memory fields for {count} profile(s).")
        return

    if ctx.message.mentions:
        target_id = str(ctx.message.mentions[0].id)
    elif target and target.isdigit():
        target_id = target
    else:
        await ctx.send("Usage: `!resetmemory <@user|id|all>`")
        return

    payload = _clear_memory_fields(_read_memory_payload(target_id))
    _write_memory_payload(target_id, payload)
    await ctx.send(f"Memory fields cleared for `{target_id}`.")


@bot.command(name="resetrelationship")
async def reset_relationship(ctx, target: str | None = None):
    if not await _ensure_owner_ctx(ctx):
        return
    if target is None and not ctx.message.mentions:
        await ctx.send("Usage: `!resetrelationship <@user|id|all>`")
        return

    if target and target.lower() == "all":
        count = 0
        for user_id in _iter_memory_file_user_ids():
            payload = _reset_relationship_fields(_read_memory_payload(user_id))
            _write_memory_payload(user_id, payload)
            count += 1
        if count == 0:
            await ctx.send(f"No memory profiles found at {MEMORY_DIR}.")
            return
        await ctx.send(f"Relationship reset for {count} profile(s).")
        return

    if ctx.message.mentions:
        target_id = str(ctx.message.mentions[0].id)
    elif target and target.isdigit():
        target_id = target
    else:
        await ctx.send("Usage: `!resetrelationship <@user|id|all>`")
        return

    payload = _reset_relationship_fields(_read_memory_payload(target_id))
    _write_memory_payload(target_id, payload)
    await ctx.send(f"Relationship reset for `{target_id}`.")


@bot.command(name="mention")
async def mention_cmd(ctx, *, greeting: str = ""):
    if not await _ensure_owner_ctx(ctx):
        return
    if not ctx.message.mentions:
        await ctx.send("Usage: `!mention <@user> [context]`")
        return

    target_user = ctx.message.mentions[0]
    prompt = (
        f"Offer a short regal greeting to <@{target_user.id}> in 1-2 sentences. "
        f"If useful, incorporate this context: {greeting.strip() or 'none'}."
    )
    result = await query_orchestrator(
        user_id=str(ctx.author.id),
        message_content=prompt,
        is_owner=True,
        relationship_score=100,
        mentioned_user_ids=[str(target_user.id)],
    )
    text = (result or {}).get("text", "A greeting from my court to yours.").strip()
    await ctx.send(f"{target_user.mention} {text}")


# ────────────────────────────────────────────────────────────────────
# Startup & Shutdown
# ────────────────────────────────────────────────────────────────────


async def main():
    """Start the Discord bot."""
    logger.info("")
    logger.info("╔════════════════════════════════════╗")
    logger.info("║   Koroki Discord Bot - Phase 7     ║")
    logger.info("║       First Contact Initiated      ║")
    logger.info("╚════════════════════════════════════╝")
    logger.info("")
    
    async with bot:
        try:
            await bot.start(DISCORD_TOKEN)
        except discord.errors.LoginFailure:
            logger.error("Failed to authenticate with Discord. Check DISCORD_TOKEN.")
            raise
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot terminated by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        exit(1)
