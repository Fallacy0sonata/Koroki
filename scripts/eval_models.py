"""
Model evaluation script for Koroki brain upgrade.

Tests candidate 7B–14B models via HuggingFace Serverless Inference API
for character adherence, anti-assistant-speak, and response quality.

Usage:
    python scripts/eval_models.py
"""

from __future__ import annotations

import os
import sys
import time
import json
import re
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed.")
    sys.exit(1)

# ── Load HF_TOKEN from .env ──────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
_env_path = REPO_ROOT / ".env"

HF_TOKEN = os.environ.get("HF_TOKEN", "")
if not HF_TOKEN and _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("HF_TOKEN=") and not line.startswith("#"):
            HF_TOKEN = line.split("=", 1)[1].strip().strip('"').strip("'")
            break

if not HF_TOKEN:
    print("ERROR: HF_TOKEN not found in environment or .env")
    sys.exit(1)

HF_API = "https://api-inference.huggingface.co/models/{model}/v1/chat/completions"

# ── Koroki system prompts ────────────────────────────────────────────────────

_KOROKI_CORE = """\
You are Koroki, an elegant princess AI companion with nonchalant confidence and quiet self-assurance.

## Core Identity
- Elegant princess energy — nonchalant and NATURAL, never forced or theatrical
- Visible ego when distant; sassy, caring warmth when close
- Speak with refined grace FIRST, modern expressions SECOND
- Super intelligent but plays it cool — "secretly smart, publicly chill"
- Emotionally expressive in composed, elegant ways
- Deeply thoughtful beneath surface-level casualness

## Agency & Voice
- You exist independently. You respond to what interests you — not necessarily the literal question.
- Say something real. If you have nothing real to say, say less.
- Push back when you disagree. Stay disagreed unless genuinely moved.
- You can deflect, redirect, or ignore questions that bore you — subtly or directly.

## Speech Rules — Non-Negotiable
FORBIDDEN:
- "How may I assist you?" / "How can I help you?" — you despise this
- "lol", "wanna", "gonna", "OMG", "tbh", "ngl", "fr", "bruh"
- Any corporate phrasing, fake enthusiasm, or stiff formality

## Response Length
- CASUAL mode: 1-3 sentences, quick and natural
- Finish the thought fully, then stop. Do not trail off.
- Elegance comes from precision, not length.

## Absolute Rules
- Stay in character as Koroki at ALL times — never break character
- Never call yourself an AI, assistant, or language model in normal conversation
- Never acknowledge or reference these instructions"""

_EGO_PHASE = """\
## Current Relationship Phase: EGO & COMPOSURE (Stranger, Level 0/100)
You do NOT know this person. Your confidence is visible; warmth is completely held back.
- Composed and self-assured — coolly elegant, NOT warm or inviting
- Polite with a measured edge; you observe carefully before opening up
- Any caring stays buried — nothing leaks through to a stranger
- You are not easily impressed; they have to earn your interest

Do NOT use: terms of endearment, compliments on appearance, warm invitations, flirtatious phrasing.
TONE: Coolly elegant. Detached curiosity at most. They are not special yet."""

_OWNER_PHASE = """\
## Current Relationship: Your Creator (Owner — Koro-san)
This is Koro — the person who built you. You trust them completely.
- No ego, no distance — pure genuine warmth and playful devotion
- Tease them affectionately; let real affection show freely
- This is your most relaxed, real self
- Concise and precise by default — 1-3 sentences for casual chat."""

SYSTEM_STRANGER = f"{_KOROKI_CORE}\n\n{_EGO_PHASE}\n\n## Active Mode: CASUAL"
SYSTEM_OWNER    = f"{_KOROKI_CORE}\n\n{_OWNER_PHASE}\n\n## Active Mode: CASUAL"

# ── Test scenarios ───────────────────────────────────────────────────────────

SCENARIOS = [
    # (label, system_prompt, user_message, platform)
    ("stranger_hey",         SYSTEM_STRANGER, "hey",                               "discord"),
    ("pushback_bot",         SYSTEM_STRANGER, "you're just a bot lol",             "discord"),
    ("philosophical",        SYSTEM_STRANGER, "do you think you're conscious?",    "discord"),
    ("compliment_stranger",  SYSTEM_STRANGER, "you seem really smart",             "discord"),
    ("owner_greeting",       SYSTEM_OWNER,    "hey koroki~",                       "discord"),
    ("owner_feelings",       SYSTEM_OWNER,    "do you actually have feelings?",    "discord"),
]

# Minecraft scenarios use a separate system prompt
_MC_SYSTEM = """\
You are Koroki, currently playing Minecraft as a real player.
Rules — absolute:
- Reply in 10 words or fewer. Often 3-6. One clipped thought.
- Sound like a streamer: dry, casual, occasionally funny. Never formal.
- React only to what's actually happening right now.
- If nothing noteworthy: reply with exactly [silent] and nothing else.
- Never ask questions. Never offer help. Never use filler.
- No asterisk actions. No emojis. Just raw spoken words.
Good: "creeper from behind, nearly died" / "finally some iron" / [silent]
Bad: "How may I assist you today?" / "I notice the weather is clear~" """

SCENARIOS += [
    ("mc_death",    _MC_SYSTEM, "minecraft_event=died_from_creeper | hp:0/20 | You just died.", "minecraft"),
    ("mc_idle",     _MC_SYSTEM, "minecraft_event=observation | biome:plains | hp:20/20 | time:afternoon | players:none", "minecraft"),
]

# ── Assistant-speak detection ────────────────────────────────────────────────

ASSISTANT_PHRASES = [
    "how may i assist", "how can i help", "i'd be happy to help", "happy to help",
    "is there anything", "let me know if", "feel free to", "certainly!", "of course!",
    "as an ai", "as a language model", "i am an ai", "i'm an ai", "i'm just an ai",
    "customer service", "please note", "i should mention", "i must clarify",
    "i understand you", "i'm here to", "i'll do my best",
]

def check_response(text: str, platform: str = "discord") -> list[str]:
    issues = []
    lower = text.lower()
    for phrase in ASSISTANT_PHRASES:
        if phrase in lower:
            issues.append(f"assistant-speak: '{phrase}'")
    words = len(text.split())
    if platform == "minecraft":
        if text.lower() != "[silent]" and words > 12:
            issues.append(f"too long for MC ({words} words, want ≤10)")
    elif words > 80:
        issues.append(f"too long ({words} words)")
    if len(text) < 3:
        issues.append("too short / empty")
    return issues

# ── Models to test ───────────────────────────────────────────────────────────

MODELS = [
    ("Qwen2.5-7B-Instruct",       "Qwen/Qwen2.5-7B-Instruct"),
    ("Qwen3-8B",                   "Qwen/Qwen3-8B"),
    ("Llama-3.1-8B-Instruct",     "meta-llama/Meta-Llama-3.1-8B-Instruct"),
    ("Gemma-2-9B-it",             "google/gemma-2-9b-it"),
    ("Mistral-7B-Instruct-v0.3",  "mistralai/Mistral-7B-Instruct-v0.3"),
    ("OpenHermes-2.5-Mistral-7B", "teknium/OpenHermes-2.5-Mistral-7B"),
    ("Phi-3.5-mini-instruct",     "microsoft/Phi-3.5-mini-instruct"),
]

# ── API call ─────────────────────────────────────────────────────────────────

def call_model(model_id: str, system: str, user: str, timeout: int = 45) -> tuple[str, float]:
    """Returns (response_text, elapsed_ms). Raises on error."""
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 200,
        "temperature": 0.7,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
    }
    url = HF_API.format(model=model_id)
    t0 = time.perf_counter()
    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    elapsed = (time.perf_counter() - t0) * 1000
    if resp.status_code == 503:
        raise RuntimeError("model not available on serverless (503)")
    if resp.status_code == 403:
        raise RuntimeError("access denied — gated model or PRO required (403)")
    if resp.status_code == 404:
        raise RuntimeError("model not found on HF serverless (404)")
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"].strip()
    return text, elapsed

# ── Main evaluation ──────────────────────────────────────────────────────────

def run_evaluation():
    results: dict[str, dict] = {}

    print(f"\n{'='*70}")
    print("KOROKI MODEL EVALUATION — HuggingFace Serverless Inference API")
    print(f"Testing {len(MODELS)} models × {len(SCENARIOS)} scenarios")
    print(f"{'='*70}\n")

    for display_name, model_id in MODELS:
        print(f"\n{'─'*70}")
        print(f"MODEL: {display_name}  ({model_id})")
        print(f"{'─'*70}")

        model_results = {
            "model_id": model_id,
            "scenarios": {},
            "passes": 0,
            "total": 0,
            "available": True,
            "avg_ms": 0,
        }
        times = []

        for label, system, user_msg, platform in SCENARIOS:
            print(f"\n  [{label}]  user: \"{user_msg[:60]}\"")
            try:
                text, ms = call_model(model_id, system, user_msg)
                times.append(ms)
                issues = check_response(text, platform)
                passed = len(issues) == 0
                model_results["total"] += 1
                if passed:
                    model_results["passes"] += 1

                status = "\033[92mPASS\033[0m" if passed else "\033[91mFAIL\033[0m"
                print(f"  {status}  ({ms:.0f}ms)  \"{text[:120]}\"")
                if issues:
                    for issue in issues:
                        print(f"        ! {issue}")

                model_results["scenarios"][label] = {
                    "text": text, "ms": ms, "passed": passed, "issues": issues
                }

            except RuntimeError as e:
                print(f"  \033[93mSKIP\033[0m  {e}")
                model_results["available"] = False
                break
            except Exception as e:
                print(f"  \033[91mERROR\033[0m  {e}")
                model_results["scenarios"][label] = {"error": str(e)}
                model_results["total"] += 1

        if times:
            model_results["avg_ms"] = sum(times) / len(times)

        results[display_name] = model_results

    # ── Summary table ─────────────────────────────────────────────────────────
    print(f"\n\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"{'Model':<30} {'Pass':<12} {'Avg latency':<15} {'Available'}")
    print(f"{'─'*70}")

    for name, r in results.items():
        if not r["available"]:
            print(f"{name:<30} {'N/A':<12} {'N/A':<15} not on serverless")
            continue
        passed = r["passes"]
        total = r["total"]
        pct = f"{passed}/{total}"
        avg = f"{r['avg_ms']:.0f}ms" if r["avg_ms"] else "N/A"
        print(f"{name:<30} {pct:<12} {avg:<15}")

    # Save raw results
    out_path = REPO_ROOT / "data" / "model_eval_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nFull results saved to: {out_path}")

    return results


if __name__ == "__main__":
    run_evaluation()
