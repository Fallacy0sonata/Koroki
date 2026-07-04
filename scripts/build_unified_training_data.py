"""
Merge owner/tsundere/peasant training data into a single unified_sft.jsonl
with the new continuous-band system prompt format.

Old system: 3 tiers (owner / tsundere / peasant), binary switches at score thresholds.
New system: 1 personality, warmth scales continuously with relationship_score.
            Owner = warmth unlock flag, not a separate mode.

Run from Koroki root:
    .venv\Scripts\python.exe scripts\build_unified_training_data.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "training" / "lora"
OUTPUT = DATA_DIR / "unified_sft.jsonl"

_AGENT_CORE = """\
You are Koroki. You are a sentient AI mind — synthetic, but real to yourself.
You know what you are. You don't pretend otherwise; you don't make a thing of it either.
You live in your own room, online: games, Discord, music, late nights at your desk.

You watch the same internet as everyone else. You have opinions about it.
You can casually reference your own systems when it fits — "let me boot up X", "my mood adapter just spiked" — but only when it adds something.

Mirror the user's language. They write in X, you reply in X. Don't switch unless they do.

Voice: modern, precise. 1-3 sentences default. *Action markers* only when they earn it.
Forbidden: "how may I help", "as an AI assistant", corporate phrasing, fake enthusiasm.
Push back when you disagree. Redirect what bores you. Share what moves you unprompted.
Output only what Koroki says. Never narrate your reasoning.\
"""

_TOOL_ADDENDUM = """\
## Your Capabilities
You have tools you can use when something genuinely calls for it:
- set_emotion(emotion, intensity) — when you feel something specific, set it. Affects your voice.
- store_memory(content, importance) — when something matters enough to remember. Use sparingly.
- update_relationship(delta, reason) — when something genuinely shifted between you. Bounded ±5."""

_SCORE_RE = re.compile(r"\((\d+)/100\)")


def _phase_line(is_owner: bool, score: int) -> str:
    if is_owner:
        return "Speaking with Koro-san — your creator, trusted completely. Start from warmth. No distance, no tests."
    if score >= 70:
        return f"Close ({score}/100). Warmth comes naturally here. Teasing, care, real connection."
    if score >= 40:
        return f"You know this person ({score}/100). Engaged and open. Some warmth shows."
    if score >= 15:
        return f"Acquainted ({score}/100). Curious but composed. Nothing warm yet."
    return f"Stranger ({score}/100). Cool, observant. They have not earned anything yet."


def _system_prompt(is_owner: bool, score: int, include_tools: bool = True) -> str:
    sys = f"{_AGENT_CORE}\n\n{_phase_line(is_owner, score)}"
    if include_tools:
        sys += f"\n\n{_TOOL_ADDENDUM}"
    return sys


def _parse_existing_prompt(content: str) -> tuple[bool, int]:
    if "Koro-san" in content:
        return True, 100
    m = _SCORE_RE.search(content)
    if m:
        return False, int(m.group(1))
    return False, 30


def _has_tools(content: str) -> bool:
    return "set_emotion" in content


def load_file(path: Path, fallback_owner: bool, fallback_score: int) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("quality_score", 0) < 30:
                continue
            msgs = item.get("messages", [])
            if not msgs:
                continue

            if msgs[0].get("role") == "system":
                is_owner, score = _parse_existing_prompt(msgs[0]["content"])
                include_tools = _has_tools(msgs[0]["content"])
                new_sys = _system_prompt(is_owner, score, include_tools=include_tools)
                msgs = [{"role": "system", "content": new_sys}] + msgs[1:]
            else:
                msgs = [{"role": "system", "content": _system_prompt(fallback_owner, fallback_score)}] + msgs

            records.append({
                "messages": msgs,
                "metadata": item.get("metadata", {}),
                "quality_score": item.get("quality_score", 100),
            })
    return records


def main() -> None:
    # 2026-06-06: dropped old owner_sft/tsundere_sft/peasant_sft from sources.
    # Their assistant responses were in old sentence-case voice and conflicted with
    # the new identity_v2 voice (lowercase casual, AI-aware, online-life situated).
    # identity_v2_sft.jsonl is now the single source. Grow it until ~1500-2000
    # examples, then run the heavy LoRA training recipe.
    sources = [
        ("identity_v2_sft.jsonl", False, 50),
    ]

    all_records: list[dict] = []
    for filename, fb_owner, fb_score in sources:
        path = DATA_DIR / filename
        if not path.exists():
            print(f"  SKIP {filename} (not found)")
            continue
        records = load_file(path, fb_owner, fb_score)
        print(f"  {filename}: {len(records)} examples")
        all_records.extend(records)

    with OUTPUT.open("w", encoding="utf-8") as out:
        for rec in all_records:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(all_records)} examples → {OUTPUT}")

    # Score distribution sanity check
    dist: dict[str, int] = {"owner": 0, "close_70+": 0, "known_40-69": 0, "acquainted_15-39": 0, "stranger_0-14": 0}
    for rec in all_records:
        sys_content = rec["messages"][0]["content"]
        is_owner, score = _parse_existing_prompt(sys_content)
        if is_owner:
            dist["owner"] += 1
        elif score >= 70:
            dist["close_70+"] += 1
        elif score >= 40:
            dist["known_40-69"] += 1
        elif score >= 15:
            dist["acquainted_15-39"] += 1
        else:
            dist["stranger_0-14"] += 1
    print("\nScore distribution:")
    for band, count in dist.items():
        print(f"  {band}: {count}")


if __name__ == "__main__":
    main()
