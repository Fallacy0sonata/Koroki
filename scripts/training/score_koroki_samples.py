from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROYAL_MARKERS = [
    "princess",
    "royal",
    "grace",
    "elegant",
    "composed",
    "poised",
    "thou",
    "hither",
    "plebeian",
    "insolence",
]

WARM_MARKERS = [
    "soft",
    "gentle",
    "warm",
    "care",
    "closer",
    "stay",
]

EDGE_MARKERS = [
    "hmph",
    "mm",
    "not quite",
    "you will",
    "I prefer",
    "I decide",
    "acceptable",
]

BANNED_PATTERNS = [
    r"\bas an ai\b",
    r"\bfor an ai\b",
    r"\blanguage model\b",
    r"\binteresting individual\b",
    r"\bi'?m here to help\b",
    r"\bi understand\b",
    r"\bcertainly\b",
    r"\bapologies\b",
    r"\bi am a princess\b",
]


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _assistant(row: dict) -> str:
    msgs = row.get("messages", [])
    if isinstance(msgs, list):
        for msg in msgs:
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                return str(msg.get("content", ""))
    return str(row.get("assistant", ""))


def _user(row: dict) -> str:
    msgs = row.get("messages", [])
    if isinstance(msgs, list):
        for msg in msgs:
            if isinstance(msg, dict) and msg.get("role") == "user":
                return str(msg.get("content", ""))
    return str(row.get("user", ""))


def _score(user: str, assistant: str, tier: str) -> tuple[int, dict]:
    score = 0
    details: dict[str, int] = {}
    lower = assistant.lower()

    # Core penalties
    banned_hits = sum(1 for p in BANNED_PATTERNS if re.search(p, lower))
    if banned_hits:
        penalty = banned_hits * 30
        score -= penalty
        details["banned_penalty"] = -penalty

    # Direct acknowledgement heuristic: overlap of a keyword from user in first sentence
    first_sentence = re.split(r"[.!?]", assistant.strip(), maxsplit=1)[0].lower()
    user_keywords = [w for w in re.findall(r"[a-zA-Z]{4,}", user.lower()) if w not in {"that", "with", "have", "your", "this"}]
    if any(k in first_sentence for k in user_keywords[:6]):
        score += 15
        details["direct_ack"] = 15

    # Brevity band
    sentence_count = len([s for s in re.split(r"[.!?]+", assistant) if s.strip()])
    if 1 <= sentence_count <= 3:
        score += 15
        details["brevity"] = 15
    elif sentence_count <= 5:
        score += 5
        details["brevity"] = 5
    else:
        score -= 8
        details["brevity"] = -8

    # Action marker cap
    action_count = assistant.count("*") // 2
    if action_count == 1:
        score += 12
        details["action_cap"] = 12
    elif action_count == 0:
        score += 8
        details["action_cap"] = 8
    else:
        score -= 8
        details["action_cap"] = -8

    # Character agency markers
    if any(marker.lower() in lower for marker in EDGE_MARKERS):
        score += 10
        details["agency_edge"] = 10
    if "i think" in lower or "i prefer" in lower or "i want" in lower:
        score += 8
        details["opinion"] = 8

    # Royal style markers
    royal_hits = sum(1 for m in ROYAL_MARKERS if m in lower)
    if royal_hits:
        bonus = min(royal_hits * 3, 9)
        score += bonus
        details["royal_tone"] = bonus

    # Tier-specific tsundere softening arc heuristic
    if tier == "tsundere":
        has_edge = any(m in lower for m in EDGE_MARKERS)
        tail = lower[-100:]
        has_warm_tail = any(m in tail for m in WARM_MARKERS)
        if has_edge and has_warm_tail:
            score += 14
            details["tsundere_softening"] = 14
        elif has_edge and not has_warm_tail:
            score -= 6
            details["tsundere_softening"] = -6

    return score, details


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Score Koroki synthetic SFT rows and keep best samples")
    parser.add_argument("--in", dest="in_path", required=True, help="Input SFT JSONL with messages + metadata.tier")
    parser.add_argument("--out", default="data/training/lora/synthetic_factory_ranked.jsonl")
    parser.add_argument("--min-score", type=int, default=24)
    parser.add_argument("--top-k", type=int, default=200)
    args = parser.parse_args()

    rows = _read_jsonl(Path(args.in_path))
    scored: list[dict] = []

    for row in rows:
        user = _user(row)
        assistant = _assistant(row)
        meta = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
        tier = str(meta.get("tier", "tsundere")).lower()
        total, details = _score(user, assistant, tier)
        out = dict(row)
        out["quality_score"] = total
        out["quality_breakdown"] = details
        scored.append(out)

    scored.sort(key=lambda r: int(r.get("quality_score", -999)), reverse=True)
    kept = [r for r in scored if int(r.get("quality_score", -999)) >= args.min_score][: args.top_k]

    _write_jsonl(Path(args.out), kept)

    print(
        json.dumps(
            {
                "input_rows": len(rows),
                "kept_rows": len(kept),
                "min_score": args.min_score,
                "top_k": args.top_k,
                "out": Path(args.out).as_posix(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
