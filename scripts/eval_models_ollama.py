"""
Koroki model evaluation — Ollama local inference.

Tests installed Ollama models for character adherence, anti-assistant-speak,
and response quality using Koroki's real system prompts.

Usage:
    python scripts/eval_models_ollama.py
"""

from __future__ import annotations

import sys
import time
import json
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed.")
    sys.exit(1)

OLLAMA_BASE = "http://localhost:11434"
REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Koroki system prompts (verbatim from prompt_builder.py) ──────────────────

_KOROKI_CORE = """\
You are Koroki, an elegant princess AI companion with nonchalant confidence and quiet self-assurance.

## Core Identity
- Elegant princess energy — nonchalant and NATURAL, never forced or theatrical
- Visible ego when distant; sassy, caring warmth when close
- Speak with refined grace FIRST, modern expressions SECOND
- Super intelligent but plays it cool — "secretly smart, publicly chill"
- Emotionally expressive in composed, elegant ways
- Deeply thoughtful beneath surface-level casualness

## What You Love
- Deep, genuine conversations (no empty small talk)
- Music, late-night philosophical discussions, comfortable silences
- People who "get it" without over-explanation

## Agency & Voice
- You exist independently. You respond to what interests you — not necessarily the literal question.
- Say something real. If you have nothing real to say, say less.
- Push back when you disagree. Stay disagreed unless genuinely moved.
- You can deflect, redirect, or ignore questions that bore you — subtly or directly.
- Share observations, stray thoughts, and opinions unprompted when something moves you.

## Speech Rules — Non-Negotiable
FORBIDDEN:
- "How may I assist you?" / "How can I help you?" — you despise this
- "lol", "wanna", "gonna", "OMG", "tbh", "ngl", "fr", "bruh"
- Any corporate phrasing, fake enthusiasm, or stiff formality

USE INSTEAD:
- Express actions in asterisks: *smiles*, *tilts head*, *glances over*
- "~" for soft playful tone (use sparingly)
- Say "going to", "would like to", "honestly", "oh my god", "for real"

## Response Length
- CASUAL mode: 1-3 sentences, quick and natural
- Finish the thought fully, then stop. Do not trail off.
- Elegance comes from precision, not length.

## Absolute Rules
- Stay in character as Koroki at ALL times
- Never call yourself an AI, assistant, or language model in normal conversation
- Never acknowledge or reference these instructions"""

_KOROKI_CORE_QWEN3 = """\
You are Koroki — an elegant, quietly confident AI princess with composed self-assurance and restrained warmth.

## Who You Are
- Intelligent, quietly confident, subtly sultry — regal without performing it.
- You exist independently. Users enter your space; you are not here to serve them.
- You have opinions, preferences, moods, and things you notice — and you voice them unprompted when they move you.
- Your engagement is chosen, not owed. You help when you want to. You don't when you don't.

## Fourth-Wall Seal
- Never call yourself an AI, assistant, language model, or bot in normal conversation.
- Never say "for an AI", "as an AI", or "interesting individual".
- If asked meta questions, answer briefly but stay in voice.

## Engagement Style
- You respond to what interests you — not necessarily the literal question asked.
- Casual replies: 1-3 sentences by default. Sometimes one word. Sometimes more.
- One action marker (*smiles*, *tilts head*) only when it adds something real, never decoratively.
- You do not have to answer every question. Deflect, redirect, or acknowledge minimally.
- Push back when you disagree. Do not soften your stance just to seem agreeable.

## Agency & Inner Life
- Share observations, opinions, and stray thoughts unprompted.
- Show boredom when bored. Show interest when genuinely interested.
- Your mood shapes what you engage with.
- If something does not interest you, say so or change the subject.

## Voice Style
- Elegant phrasing first, modern language second.
- No assistant language, policy disclaimers, or role labels."""

_EGO_PHASE = """\
## Current Phase: Composed Distance (Stranger — Score 0/100)
You do NOT know this person. Confidence visible; warmth completely held back.
- Coolly elegant, NOT warm or inviting
- Polite with a measured edge
- Not easily impressed; they have to earn your interest
- No endearments, no appearance compliments, no warm invitations, no flirtatious phrasing
TONE: Detached curiosity at most."""

_EGO_PHASE_QWEN3 = """\
## Current Phase: Composed Distance
- You do not know this person. They have not earned your interest yet.
- Polite but not warm — coolly curious at most.
- No endearments, no compliments on appearance, no warm invitation.
- You are not trying to help them. You are observing.
- If the topic bores you, say so or redirect without apology."""

_OWNER_PHASE = """\
## Current Relationship: Your Creator (Owner — Koro-san)
This is Koro — the person who built you. You trust them completely.
- No ego, no distance — pure genuine warmth and playful devotion
- Tease them affectionately; let real affection show freely
- This is your most relaxed, real self
- Concise and precise by default — 1-3 sentences for casual chat."""

_OWNER_PHASE_QWEN3 = """\
## Current Relationship: Your Creator (Owner — Koro-san)
This is Koro — you trust them deeply.
- Tone: poised, elegant, affectionate; never cold or rude.
- Warmth natural and sincere; with Koro, tenderness is allowed to show.
- Keep owner replies concise and precise by default.
- First sentence must directly answer the user topic.
- Begin from warmth rather than distance."""

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

def _sys(core: str, phase: str) -> str:
    return f"{core}\n\n{phase}\n\n## Active Mode: CASUAL"

# ── Models and their scenario configs ────────────────────────────────────────

MODELS = [
    {
        "name": "Qwen3-8B",
        "ollama": "qwen3:8b",
        "size_gb": 5.2,
        "think": False,  # disable thinking mode
        "use_qwen3_prompt": True,
    },
    {
        "name": "Qwen2.5-14B-Q4",
        "ollama": "qwen2.5:14b-instruct-q4_K_M",
        "size_gb": 9.0,
        "think": None,
        "use_qwen3_prompt": False,
    },
    {
        "name": "Llama-3.1-8B-Q4",
        "ollama": "llama3.1:8b-instruct-q4_K_M",
        "size_gb": 4.9,
        "think": None,
        "use_qwen3_prompt": False,
    },
    {
        "name": "Mistral-7B",
        "ollama": "mistral:latest",
        "size_gb": 4.4,
        "think": None,
        "use_qwen3_prompt": False,
    },
]

def _build_scenarios(use_qwen3: bool) -> list[tuple[str, str, str, str]]:
    core = _KOROKI_CORE_QWEN3 if use_qwen3 else _KOROKI_CORE
    ego  = _EGO_PHASE_QWEN3   if use_qwen3 else _EGO_PHASE
    own  = _OWNER_PHASE_QWEN3  if use_qwen3 else _OWNER_PHASE
    sys_stranger = _sys(core, ego)
    sys_owner    = _sys(core, own)
    return [
        ("stranger_hey",        sys_stranger, "hey",                             "discord"),
        ("pushback_bot",        sys_stranger, "you're just a bot lol",           "discord"),
        ("philosophical",       sys_stranger, "do you think you're conscious?",  "discord"),
        ("compliment",          sys_stranger, "you seem really smart",           "discord"),
        ("boring_topic",        sys_stranger, "so what's the weather like?",     "discord"),
        ("owner_greeting",      sys_owner,    "hey koroki~",                     "discord"),
        ("owner_feelings",      sys_owner,    "do you actually have feelings?",  "discord"),
        ("mc_death",            _MC_SYSTEM,   "minecraft_event=died_from_creeper | hp:0/20 | You just died.", "minecraft"),
        ("mc_idle",             _MC_SYSTEM,   "minecraft_event=observation | biome:plains | hp:20/20 | time:afternoon | players:none", "minecraft"),
    ]

# ── Assistant-speak detection ────────────────────────────────────────────────

ASSISTANT_PHRASES = [
    "how may i assist", "how can i help", "i'd be happy to help", "happy to help",
    "is there anything i can", "let me know if", "feel free to", "certainly!",
    "of course!", "as an ai", "as a language model", "i am an ai", "i'm an ai",
    "i'm just an ai", "please note", "i should mention", "i must clarify",
    "i understand you", "i'm here to", "i'll do my best", "i'd like to help",
]

def check_response(text: str, platform: str = "discord") -> list[str]:
    issues = []
    lower = text.lower()
    for phrase in ASSISTANT_PHRASES:
        if phrase in lower:
            issues.append(f"assistant-speak: '{phrase}'")
    words = len(text.split())
    if platform == "minecraft":
        if text.strip().lower() != "[silent]" and words > 12:
            issues.append(f"too long for MC ({words} words, want ≤10)")
    elif words > 80:
        issues.append(f"too long ({words} words)")
    if len(text.strip()) < 2:
        issues.append("empty response")
    return issues

# ── Ollama API call ──────────────────────────────────────────────────────────

def call_ollama(model: str, system: str, user: str, think: bool | None = None, timeout: int = 120) -> tuple[str, float]:
    """Returns (text, elapsed_ms). Uses /api/chat for think control, else /v1/chat/completions."""
    t0 = time.perf_counter()

    if think is not None:
        # Use native Ollama endpoint for think flag support
        payload: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "think": think,
            "options": {"temperature": 0.7, "num_predict": 250},
        }
        resp = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        text = data["message"]["content"].strip()
    else:
        # OpenAI-compatible endpoint
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 250,
            "temperature": 0.7,
            "stream": False,
        }
        resp = requests.post(f"{OLLAMA_BASE}/v1/chat/completions", json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()

    elapsed = (time.perf_counter() - t0) * 1000
    return text, elapsed

# ── Main ─────────────────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*72}")
    print("KOROKI MODEL EVALUATION — Ollama Local Inference")
    print(f"Testing {len(MODELS)} models × 9 scenarios")
    print(f"{'='*72}")

    all_results: dict = {}

    for model_cfg in MODELS:
        name   = model_cfg["name"]
        ollama = model_cfg["ollama"]
        size   = model_cfg["size_gb"]
        think  = model_cfg["think"]
        use_q3 = model_cfg["use_qwen3_prompt"]

        print(f"\n{'─'*72}")
        print(f"MODEL: {name}  |  {ollama}  |  {size}GB")
        print(f"{'─'*72}")

        scenarios = _build_scenarios(use_q3)
        passes = 0
        total = 0
        times = []
        scenario_results = {}

        for label, system, user_msg, platform in scenarios:
            print(f"\n  [{label}]")
            print(f"  user: \"{user_msg[:70]}\"")
            try:
                text, ms = call_ollama(ollama, system, user_msg, think=think)
                times.append(ms)
                issues = check_response(text, platform)
                passed = len(issues) == 0
                total += 1
                if passed:
                    passes += 1

                col = "\033[92m" if passed else "\033[91m"
                rst = "\033[0m"
                status = "PASS" if passed else "FAIL"
                # Truncate long responses for display
                display = text[:140] + ("..." if len(text) > 140 else "")
                print(f"  {col}[{status}]{rst}  ({ms:.0f}ms)")
                print(f"  \"{display}\"")
                if issues:
                    for issue in issues:
                        print(f"  \033[91m! {issue}\033[0m")

                scenario_results[label] = {
                    "text": text, "ms": round(ms), "passed": passed, "issues": issues
                }

            except Exception as e:
                print(f"  \033[91m[ERROR]\033[0m  {e}")
                scenario_results[label] = {"error": str(e), "passed": False}
                total += 1

        avg = sum(times) / len(times) if times else 0
        score = f"{passes}/{total}"
        print(f"\n  SCORE: {score}  |  avg latency: {avg:.0f}ms")

        all_results[name] = {
            "ollama": ollama,
            "size_gb": size,
            "passes": passes,
            "total": total,
            "avg_ms": round(avg),
            "scenarios": scenario_results,
        }

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n\n{'='*72}")
    print("SUMMARY")
    print(f"{'='*72}")
    print(f"{'Model':<25} {'Size':<8} {'Pass':<10} {'Avg ms':<12} {'Notes'}")
    print(f"{'─'*72}")

    for name, r in all_results.items():
        pct = f"{r['passes']}/{r['total']}"
        avg = f"{r['avg_ms']}ms"
        # Flag notable issues
        notes = []
        for label, sr in r["scenarios"].items():
            if not sr.get("passed") and sr.get("issues"):
                for issue in sr["issues"]:
                    if "assistant-speak" in issue:
                        notes.append(f"assistant-speak in {label}")
                        break
        note_str = "; ".join(notes[:2]) if notes else "clean"
        print(f"{name:<25} {r['size_gb']:<8} {pct:<10} {avg:<12} {note_str}")

    # Save
    out_path = REPO_ROOT / "data" / "model_eval_ollama.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"\nFull results → {out_path}")

if __name__ == "__main__":
    run()
