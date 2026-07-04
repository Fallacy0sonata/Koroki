from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


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
    r"\bhow may i assist\b",
    r"\bhow can i help\b",
    r"\bi'?d be happy to help\b",
    r"\bcustomer service\b",
]


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        out.append(json.loads(line))
    return out


def _assistant_text(row: dict) -> str:
    if isinstance(row.get("draft_assistant"), str):
        return row["draft_assistant"]
    if isinstance(row.get("assistant"), str):
        return row["assistant"]
    msgs = row.get("messages", [])
    if isinstance(msgs, list):
        for msg in msgs:
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                return str(msg.get("content", ""))
    return ""


def _user_text(row: dict) -> str:
    if isinstance(row.get("user_prompt"), str):
        return row["user_prompt"]
    if isinstance(row.get("user"), str):
        return row["user"]
    msgs = row.get("messages", [])
    if isinstance(msgs, list):
        for msg in msgs:
            if isinstance(msg, dict) and msg.get("role") == "user":
                return str(msg.get("content", ""))
    return ""


def _find_reject_reason(text: str, min_chars: int) -> str | None:
    t = text.strip()
    if len(t) < min_chars:
        return f"too_short_lt_{min_chars}"
    lower = t.lower()
    for pattern in BANNED_PATTERNS:
        if re.search(pattern, lower):
            return f"banned:{pattern}"
    return None


def _to_sft(row: dict, user: str, assistant: str) -> dict:
    tier = str(row.get("tier", row.get("metadata", {}).get("tier", "tsundere"))).strip().lower()
    if tier not in {"owner", "tsundere", "peasant"}:
        tier = "tsundere"
    return {
        "messages": [
            {"role": "user", "content": user},
            {"role": "assistant", "content": assistant},
        ],
        "metadata": {
            "tier": tier,
            "source": str(row.get("source", "synthetic_factory")),
            "scenario_key": str(row.get("scenario_key", "")),
        },
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter Koroki synthetic rows and export LoRA-ready SFT JSONL")
    parser.add_argument("--in", dest="in_path", required=True, help="Input JSONL with draft_assistant/assistant/messages")
    parser.add_argument("--accepted", default="data/training/synthetic/accepted.jsonl")
    parser.add_argument("--rejected", default="data/training/synthetic/rejected.jsonl")
    parser.add_argument("--sft-out", default="data/training/lora/synthetic_factory_sft.jsonl")
    parser.add_argument("--min-chars", type=int, default=18)
    args = parser.parse_args()

    rows = _read_jsonl(Path(args.in_path))
    accepted: list[dict] = []
    rejected: list[dict] = []
    sft_rows: list[dict] = []

    for row in rows:
        assistant = _assistant_text(row)
        user = _user_text(row)
        reason = _find_reject_reason(assistant, args.min_chars)
        if not user.strip():
            reason = reason or "missing_user"
        if reason:
            rej = dict(row)
            rej["reject_reason"] = reason
            rejected.append(rej)
            continue
        accepted.append(row)
        sft_rows.append(_to_sft(row, user.strip(), assistant.strip()))

    _write_jsonl(Path(args.accepted), accepted)
    _write_jsonl(Path(args.rejected), rejected)
    _write_jsonl(Path(args.sft_out), sft_rows)

    stats = {
        "input_rows": len(rows),
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "sft_rows": len(sft_rows),
        "accepted_path": Path(args.accepted).as_posix(),
        "rejected_path": Path(args.rejected).as_posix(),
        "sft_out": Path(args.sft_out).as_posix(),
    }
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
