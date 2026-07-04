from __future__ import annotations

import argparse
import json
import re
import socket
from collections import Counter
from pathlib import Path


AI_PATTERN = re.compile(r"\b(ai|language model|as an ai|for an ai)\b", re.IGNORECASE)
PLACEHOLDER_PATTERN = re.compile(r"draft response here|todo|tbd", re.IGNORECASE)


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _is_port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _assistant_text(row: dict) -> str:
    if isinstance(row.get("draft_assistant"), str):
        return row["draft_assistant"].strip()
    if isinstance(row.get("assistant"), str):
        return row["assistant"].strip()
    msgs = row.get("messages", [])
    if isinstance(msgs, list):
        for msg in msgs:
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                return str(msg.get("content", "")).strip()
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight checks before LoRA training launch")
    parser.add_argument("--tasks", default="data/training/synthetic/teacher_student_tasks.jsonl")
    parser.add_argument("--sft", default="data/training/lora/synthetic_factory_sft.jsonl")
    parser.add_argument("--owner-min", type=int, default=30)
    args = parser.parse_args()

    tasks_path = Path(args.tasks)
    if not tasks_path.exists():
        raise FileNotFoundError(f"Task file not found: {tasks_path}")

    tasks = _read_jsonl(tasks_path)
    drafted_rows = [r for r in tasks if _assistant_text(r)]
    placeholder_rows = [r for r in drafted_rows if PLACEHOLDER_PATTERN.search(_assistant_text(r))]

    tier_counts = Counter(str(r.get("tier", "unknown")).strip().lower() for r in drafted_rows)

    ai_hits = 0
    sft_path = Path(args.sft)
    if sft_path.exists():
        for row in _read_jsonl(sft_path):
            text = _assistant_text(row)
            if AI_PATTERN.search(text):
                ai_hits += 1

    services_open = {
        "brain_9881": _is_port_open("127.0.0.1", 9881),
        "tts_9880": _is_port_open("127.0.0.1", 9880),
        "orchestrator_9882": _is_port_open("127.0.0.1", 9882),
    }

    checks = {
        "placeholder_purge": len(placeholder_rows) == 0,
        "draft_completion": len(drafted_rows) == len(tasks) and len(tasks) > 0,
        "owner_tier_balance": tier_counts.get("owner", 0) >= args.owner_min,
        "ai_audit_clean_or_unavailable": (not sft_path.exists()) or ai_hits == 0,
        "service_lockdown": not any(services_open.values()),
    }

    report = {
        "tasks_total": len(tasks),
        "tasks_drafted": len(drafted_rows),
        "placeholder_rows": len(placeholder_rows),
        "drafted_tier_counts": dict(tier_counts),
        "sft_path": sft_path.as_posix(),
        "sft_ai_hits": ai_hits,
        "services_open": services_open,
        "checks": checks,
        "ready_to_launch": all(checks.values()),
    }

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
