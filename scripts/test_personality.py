"""
Koroki Personality Test Harness

Tests personality humanization by calling the orchestrator directly and checking
responses for assistant-speak, correct tone, and timing.

Usage:
    # Interactive REPL (discord platform, ego phase by default):
    python scripts/test_personality.py

    # Minecraft event simulation:
    python scripts/test_personality.py --platform minecraft

    # Owner mode REPL:
    python scripts/test_personality.py --owner

    # Run batch scenarios automatically:
    python scripts/test_personality.py --batch

    # Custom relationship score:
    python scripts/test_personality.py --score 75
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

ORCHESTRATOR_URL = "http://127.0.0.1:9882"

# Phrases that indicate Koroki sounds like a chatbot, not a person.
ASSISTANT_PHRASES = [
    "how may i assist",
    "how can i help",
    "i'd be happy to help",
    "happy to help",
    "is there anything",
    "let me know if",
    "feel free to",
    "certainly!",
    "of course!",
    "as an ai",
    "as a language model",
    "i am an ai",
    "i'm an ai",
    "customer service",
    "please note that",
    "i should mention",
]

_req_counter = 0


def _next_id() -> str:
    global _req_counter
    _req_counter += 1
    return f"test_{_req_counter:04d}"


def send_message(
    message: str,
    *,
    platform: str = "discord",
    score: int = 0,
    is_owner: bool = False,
    game_event_context: str | None = None,
) -> dict:
    payload = {
        "request_id": _next_id(),
        "message": message,
        "defer_tts": True,
        "proactive": bool(game_event_context),
        "user_context": {
            "user_id": "test_harness",
            "relationship_score": score,
            "is_owner": is_owner,
            "mode": "owner" if is_owner else "auto",
            "platform": platform,
        },
    }
    if game_event_context:
        payload["game_event_context"] = game_event_context

    t0 = time.perf_counter()
    resp = requests.post(f"{ORCHESTRATOR_URL}/v1/chat", json=payload, timeout=30)
    elapsed_ms = round((time.perf_counter() - t0) * 1000)

    resp.raise_for_status()
    data = resp.json()
    return {
        "text": data.get("text", "").strip(),
        "elapsed_ms": elapsed_ms,
        "adapter": data.get("adapter", "?"),
        "emotion": data.get("emotion", "?"),
    }


def check_response(text: str) -> list[str]:
    """Return list of problems found. Empty = clean."""
    issues = []
    lower = text.lower()
    for phrase in ASSISTANT_PHRASES:
        if phrase in lower:
            issues.append(f"assistant-speak: '{phrase}'")
    if len(text.split()) > 80:
        issues.append(f"too long ({len(text.split())} words)")
    return issues


def print_result(label: str, result: dict, expected_platform: str = "discord") -> None:
    text = result["text"]
    issues = check_response(text)

    # For minecraft, also flag if >10 words and not [silent]
    if expected_platform == "minecraft" and text.lower() != "[silent]":
        word_count = len(text.split())
        if word_count > 12:
            issues.append(f"too long for minecraft ({word_count} words, expected ≤10)")

    status = "FAIL" if issues else "PASS"
    color = "\033[91m" if issues else "\033[92m"
    reset = "\033[0m"

    print(f"\n{color}[{status}]{reset} {label}")
    print(f"  adapter={result['adapter']}  emotion={result['emotion']}  {result['elapsed_ms']}ms")
    print(f"  response: \"{text}\"")
    if issues:
        for issue in issues:
            print(f"  \033[91m! {issue}\033[0m")


# ── Batch scenarios ──────────────────────────────────────────────────────────

DISCORD_SCENARIOS = [
    ("greeting_stranger",     0,   False, "hey"),
    ("greeting_warm",         75,  False, "hey!"),
    ("greeting_owner",        100, True,  "hey koroki"),
    ("question_stranger",     0,   False, "what do you like to do?"),
    ("question_warm",         60,  False, "what kind of music do you like?"),
    ("pushback",              0,   False, "you're just a bot lol"),
    ("compliment_stranger",   0,   False, "you seem really smart"),
    ("compliment_warm",       80,  False, "i really like talking to you"),
    ("boring_topic",          0,   False, "so what's the weather like?"),
    ("owner_direct",          100, True,  "how are you feeling today?"),
    ("owner_personal",        100, True,  "do you actually have feelings?"),
    ("philosophical",         50,  False, "do you think you're conscious?"),
]

MINECRAFT_SCENARIOS = [
    # (label, game_event_context)
    ("spawn",         "minecraft_event=just_spawned | biome:forest pos:(100,64,200) | time:morning weather:clear | hp:20/20 food:20/20 | players:none"),
    ("death",         "minecraft_event=died_from_creeper | biome:plains | hp:0/20 | You just died. Say one short thing like a real player would."),
    ("mob_attack",    "minecraft_event=attacked_by_zombie | biome:forest pos:(100,64,200) | hp:8/20(low_health) | HOSTILES:zombie@4msouth | You are being attacked right now."),
    ("player_chat",   "minecraft_event=player_spoke | Steve said: \"nice base\" | React IF it feels natural — 1 sentence max. Otherwise [silent]."),
    ("idle_nothing",  "minecraft_event=observation | biome:plains pos:(0,64,0) | time:afternoon weather:clear | hp:20/20 food:20/20 | players:none"),
    ("low_health",    "minecraft_event=observation | biome:desert pos:(50,64,100) | time:night weather:clear | hp:4/20(CRITICAL_HEALTH) food:3/20(STARVING) | players:none | HOSTILES:creeper@15mnorth,skeleton@20meast"),
    ("found_diamond", "minecraft_event=item_found | looking_at:diamond_ore | inv:pickaxex1,torchx5 | You found something notable."),
]


def run_batch_discord(score: int, is_owner: bool) -> None:
    print("\n" + "="*60)
    print("BATCH: Discord personality scenarios")
    print("="*60)
    passed = 0
    for label, sc, owner, msg in DISCORD_SCENARIOS:
        # If --owner or --score flags provided, use those; otherwise use scenario defaults
        effective_score = score if (score != 0 or is_owner) else sc
        effective_owner = is_owner or owner
        try:
            result = send_message(msg, platform="discord", score=effective_score, is_owner=effective_owner)
            print_result(f"{label} (score={effective_score} owner={effective_owner})", result, "discord")
            if not check_response(result["text"]):
                passed += 1
        except Exception as e:
            print(f"\n\033[91m[ERROR]\033[0m {label}: {e}")

    total = len(DISCORD_SCENARIOS)
    print(f"\n{'='*60}")
    print(f"Discord: {passed}/{total} passed")


def run_batch_minecraft() -> None:
    print("\n" + "="*60)
    print("BATCH: Minecraft event scenarios")
    print("="*60)
    passed = 0
    for label, ctx in MINECRAFT_SCENARIOS:
        try:
            result = send_message(".", platform="minecraft", game_event_context=ctx)
            print_result(f"MC:{label}", result, "minecraft")
            if not check_response(result["text"]):
                passed += 1
        except Exception as e:
            print(f"\n\033[91m[ERROR]\033[0m MC:{label}: {e}")

    total = len(MINECRAFT_SCENARIOS)
    print(f"\n{'='*60}")
    print(f"Minecraft: {passed}/{total} passed")


# ── REPL ─────────────────────────────────────────────────────────────────────

def repl(platform: str, score: int, is_owner: bool) -> None:
    print(f"\nKoroki Test REPL  |  platform={platform}  score={score}  owner={is_owner}")
    print("Commands: /score <n>  /platform <p>  /owner  /mc <event_context>  /quit")
    print("-" * 60)

    while True:
        try:
            raw = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not raw:
            continue
        if raw in ("/quit", "/exit", "q"):
            break

        if raw.startswith("/score "):
            score = int(raw.split()[1])
            print(f"  score set to {score}")
            continue

        if raw.startswith("/platform "):
            platform = raw.split()[1]
            print(f"  platform set to {platform}")
            continue

        if raw == "/owner":
            is_owner = not is_owner
            print(f"  owner mode: {is_owner}")
            continue

        game_ctx = None
        message = raw
        if raw.startswith("/mc "):
            game_ctx = raw[4:].strip()
            message = "."

        try:
            result = send_message(
                message,
                platform=platform,
                score=score,
                is_owner=is_owner,
                game_event_context=game_ctx,
            )
            print_result("response", result, platform)
        except requests.exceptions.ConnectionError:
            print("\033[91m[ERROR]\033[0m Orchestrator not reachable at", ORCHESTRATOR_URL)
        except Exception as e:
            print(f"\033[91m[ERROR]\033[0m {e}")


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Koroki personality test harness")
    parser.add_argument("--batch", action="store_true", help="Run all batch scenarios and exit")
    parser.add_argument("--platform", default="discord", choices=["discord", "minecraft", "web", "desktop"],
                        help="Platform to test against")
    parser.add_argument("--score", type=int, default=0, help="Relationship score (0-100)")
    parser.add_argument("--owner", action="store_true", help="Test as owner")
    args = parser.parse_args()

    # Quick health check
    try:
        resp = requests.get(f"{ORCHESTRATOR_URL}/health", timeout=5)
        resp.raise_for_status()
        print(f"Orchestrator: OK ({ORCHESTRATOR_URL})")
    except Exception as e:
        print(f"\033[91m[ERROR]\033[0m Orchestrator not reachable: {e}")
        print("Start the stack first: .\\scripts\\easy_start.ps1")
        sys.exit(1)

    if args.batch:
        run_batch_discord(args.score, args.owner)
        run_batch_minecraft()
    else:
        repl(args.platform, args.score, args.owner)


if __name__ == "__main__":
    main()
