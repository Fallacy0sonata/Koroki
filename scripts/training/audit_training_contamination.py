from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


META_PATTERNS = [
    r"\bas an ai\b",
    r"\bfor an ai\b",
    r"\blanguage model\b",
    r"\bchatbot\b",
    r"\bvirtual assistant\b",
    r"\bi'?m here to help\b",
    r"\bi understand\b",
    r"\bcertainly\b",
    r"\bapologies\b",
    r"\bhow may i assist\b",
    r"\bhow can i help\b",
    r"\bi'?d be happy to help\b",
    r"\bcustomer service\b",
    r"\bi am a princess\b",
]


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _assistant_text(row: dict) -> str:
    if isinstance(row.get("assistant"), str):
        return row["assistant"].strip()
    if isinstance(row.get("draft_assistant"), str):
        return row["draft_assistant"].strip()
    msgs = row.get("messages", [])
    if isinstance(msgs, list):
        for msg in msgs:
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                return str(msg.get("content", "")).strip()
    return ""


def _normalize_sentence(text: str) -> str:
    first = re.split(r"[.!?]", text.strip(), maxsplit=1)[0]
    first = re.sub(r"\*[^*\n]+\*|\([^\)\n]+\)|\[[^\]\n]+\]", "", first)
    first = re.sub(r"[^a-z0-9\s]", " ", first.lower())
    return re.sub(r"\s+", " ", first).strip()


def _meta_hits(text: str) -> int:
    t = text.lower()
    return sum(1 for pat in META_PATTERNS if re.search(pat, t))


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit SFT JSONL for style contamination and assistant leakage")
    parser.add_argument("--in", dest="in_path", required=True)
    parser.add_argument("--max-meta-hits", type=int, default=0)
    parser.add_argument("--max-duplicate-ratio", type=float, default=0.12)
    parser.add_argument("--max-first-sentence-ratio", type=float, default=0.18)
    args = parser.parse_args()

    in_path = Path(args.in_path)
    if not in_path.exists():
        raise FileNotFoundError(f"Input file not found: {in_path}")

    rows = _read_jsonl(in_path)
    assistant_texts = [t for t in (_assistant_text(r) for r in rows) if t]

    meta_hits = sum(_meta_hits(t) for t in assistant_texts)

    dup_counts = Counter(assistant_texts)
    duplicate_rows = sum(c - 1 for c in dup_counts.values() if c > 1)
    duplicate_ratio = (duplicate_rows / max(len(assistant_texts), 1))

    first_counts = Counter(_normalize_sentence(t) for t in assistant_texts if _normalize_sentence(t))
    top_first_sentence, top_first_count = ("", 0)
    if first_counts:
        top_first_sentence, top_first_count = first_counts.most_common(1)[0]
    top_first_ratio = (top_first_count / max(len(assistant_texts), 1))

    checks = {
        "meta_ai_ism_clean": meta_hits <= args.max_meta_hits,
        "duplicate_ratio_ok": duplicate_ratio <= args.max_duplicate_ratio,
        "first_sentence_diversity_ok": top_first_ratio <= args.max_first_sentence_ratio,
    }

    report = {
        "input_rows": len(rows),
        "assistant_rows": len(assistant_texts),
        "meta_hits": meta_hits,
        "duplicate_rows": duplicate_rows,
        "duplicate_ratio": round(duplicate_ratio, 4),
        "top_first_sentence": top_first_sentence,
        "top_first_sentence_count": top_first_count,
        "top_first_sentence_ratio": round(top_first_ratio, 4),
        "thresholds": {
            "max_meta_hits": args.max_meta_hits,
            "max_duplicate_ratio": args.max_duplicate_ratio,
            "max_first_sentence_ratio": args.max_first_sentence_ratio,
        },
        "checks": checks,
        "pass": all(checks.values()),
    }

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
