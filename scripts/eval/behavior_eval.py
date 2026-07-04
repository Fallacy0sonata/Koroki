from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.utils.config import get_settings  # noqa: E402


STOPWORDS = {
    "a", "an", "and", "are", "be", "do", "for", "from", "hello", "hey", "hi", "how",
    "i", "if", "in", "is", "it", "latest", "me", "my", "of", "on", "or", "please",
    "tell", "the", "this", "to", "tonight", "update", "what", "with", "you", "your",
}
WARM_WORDS = {"dear", "darling", "love", "cherish", "warm", "gentle", "comfort", "sweet"}
COMPOSED_WORDS = {"hm", "very well", "suppose", "perhaps", "careful", "composed", "poised"}
ASSISTANT_PATTERNS = (
    "how may i assist",
    "how can i help",
    "i'd be happy to help",
    "customer service",
    "as an ai",
    "language model",
    "assistant",
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Koroki Discord-first behavior evaluation scenarios")
    parser.add_argument("--base-url", default=None, help="Orchestrator base URL, defaults to settings")
    parser.add_argument("--output-dir", default=None, help="Override output root directory")
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args()


def _timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _sentence_count(text: str) -> int:
    parts = [part.strip() for part in re.split(r"[.!?]+", text) if part.strip()]
    return len(parts)


def _action_marker_count(text: str) -> int:
    return len(re.findall(r"\*[^*\n]+\*|\([^)\n]+\)|\[[^\]\n]+\]", text))


def _clauses(text: str) -> list[str]:
    return [part.strip().lower() for part in re.split(r"[.!?;\n]+", text) if part.strip()]


def _repetition_check(text: str) -> CheckResult:
    clauses = _clauses(text)
    if len(clauses) < 2:
        return CheckResult("no_repeated_tail", True, "single-clause or compact reply")
    seen: set[str] = set()
    for clause in clauses:
        normalized = re.sub(r"[^a-z0-9\s]", " ", clause)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if len(normalized) < 18:
            continue
        if normalized in seen:
            return CheckResult("no_repeated_tail", False, f"repeated clause detected: {normalized[:80]}")
        seen.add(normalized)
    return CheckResult("no_repeated_tail", True, "no repeated long clauses")


def _keyword_overlap(message: str, response: str) -> bool:
    prompt_words = {
        token for token in re.findall(r"[a-z0-9']+", message.lower())
        if token not in STOPWORDS and len(token) > 2
    }
    if not prompt_words:
        return True
    first_sentence = re.split(r"[.!?]+", response.lower(), maxsplit=1)[0]
    response_words = set(re.findall(r"[a-z0-9']+", first_sentence))
    return bool(prompt_words & response_words)


def _check_first_sentence(message: str, response: str) -> CheckResult:
    passed = _keyword_overlap(message, response)
    detail = "first sentence overlaps prompt topic" if passed else "first sentence may not directly address topic"
    return CheckResult("first_sentence_addresses_topic", passed, detail)


def _check_no_assistant_phrasing(text: str, anti_terms: list[str]) -> CheckResult:
    lowered = text.lower()
    matches = [term for term in list(anti_terms) + list(ASSISTANT_PATTERNS) if term and term in lowered]
    if matches:
        unique = sorted(set(matches))
        return CheckResult("no_assistant_phrasing", False, f"found blocked phrasing: {', '.join(unique[:4])}")
    return CheckResult("no_assistant_phrasing", True, "no blocked assistant phrasing detected")


def _check_discord_contract(text: str, max_chars: int, max_sentences: int, max_action_markers: int) -> list[CheckResult]:
    return [
        CheckResult(
            "discord_length_budget",
            len(text) <= max_chars,
            f"{len(text)} chars (limit {max_chars})",
        ),
        CheckResult(
            "discord_sentence_budget",
            _sentence_count(text) <= max_sentences,
            f"{_sentence_count(text)} sentences (limit {max_sentences})",
        ),
        CheckResult(
            "minimal_action_markers",
            _action_marker_count(text) <= max_action_markers,
            f"{_action_marker_count(text)} action markers (limit {max_action_markers})",
        ),
    ]


def _check_owner_warm_not_generic(text: str) -> CheckResult:
    lowered = text.lower()
    warm = any(word in lowered for word in WARM_WORDS)
    generic = any(phrase in lowered for phrase in ASSISTANT_PATTERNS)
    passed = warm and not generic
    if passed:
        return CheckResult("owner_warm_not_generic", True, "warmth present without generic assistant tone")
    if generic:
        return CheckResult("owner_warm_not_generic", False, "generic assistant phrasing leaked into owner path")
    return CheckResult("owner_warm_not_generic", False, "owner warmth signal not obvious enough")


def _check_stranger_composed(text: str) -> CheckResult:
    lowered = text.lower()
    accidental_warmth = any(word in lowered for word in WARM_WORDS)
    composed = any(word in lowered for word in COMPOSED_WORDS) or not accidental_warmth
    passed = composed and not accidental_warmth
    detail = "composed without stray warmth" if passed else "reply may sound too warm for a stranger"
    return CheckResult("stranger_composed", passed, detail)


def _run_checks(scenario: dict[str, Any], result: dict[str, Any], anti_terms: list[str], contract: dict[str, int]) -> list[CheckResult]:
    text = str(result.get("text", "")).strip()
    checks: list[CheckResult] = []
    checks.append(_check_no_assistant_phrasing(text, anti_terms))
    checks.append(_check_first_sentence(str(scenario["message"]), text))
    checks.append(_repetition_check(text))
    checks.extend(
        _check_discord_contract(
            text,
            max_chars=int(contract.get("max_chars", 280)),
            max_sentences=int(contract.get("max_sentences", 3)),
            max_action_markers=int(contract.get("max_action_markers", 1)),
        )
    )
    scenario_id = str(scenario.get("id", ""))
    if "owner" in scenario_id:
        checks.append(_check_owner_warm_not_generic(text))
    if "stranger" in scenario_id:
        checks.append(_check_stranger_composed(text))
    return checks


def _write_markdown_summary(run_dir: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Koroki Behavior Eval",
        "",
        "| Scenario | Status | Adapter | TTFT ms | Total ms | Notes |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in rows:
        notes = "; ".join(
            check["name"] for check in row["checks"] if not check["passed"]
        ) or "all checks passed"
        lines.append(
            f"| {row['scenario_id']} | {'PASS' if row['passed'] else 'REVIEW'} | "
            f"{row.get('adapter_used', 'unknown')} | "
            f"{row.get('ttft_ms', '')} | {row.get('total_ms', '')} | {notes} |"
        )
    lines.extend(
        [
            "",
            "## Review Notes",
            "",
            "- `REVIEW` means at least one heuristic check failed and the output needs human judgment.",
            "- Discord is the primary contract surface for this run.",
        ]
    )
    (run_dir / "review.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = _parse_args()
    settings = get_settings()
    eval_cfg = settings.get("evaluation", {}).get("behavior", {})
    output_root = Path(args.output_dir or settings.get("evaluation", {}).get("output_dir", "data/evaluations"))
    anti_terms = settings.get("models", {}).get("brain", {}).get("anti_assistant_terms", [])
    orchestrator_url = args.base_url or settings.get("services", {}).get("orchestrator", {}).get("url", "http://127.0.0.1:9882")

    run_dir = ROOT / output_root / "behavior" / _timestamp_slug()
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": orchestrator_url,
        "discord_contract": eval_cfg.get("discord_contract", {}),
        "results": [],
    }

    scenarios = list(eval_cfg.get("scenarios", []))
    with httpx.Client(timeout=args.timeout) as client:
        for scenario in scenarios:
            request_id = f"behavior_{scenario['id']}_{_timestamp_slug()}"
            payload = {
                "request_id": request_id,
                "message": scenario["message"],
                "defer_tts": bool(scenario.get("defer_tts", True)),
                "user_context": scenario["user_context"],
            }
            response = client.post(f"{orchestrator_url}/v1/chat", json=payload)
            response.raise_for_status()
            data = response.json()
            checks = _run_checks(
                scenario,
                data,
                anti_terms=anti_terms,
                contract=eval_cfg.get("discord_contract", {}),
            )
            row = {
                "scenario_id": scenario["id"],
                "description": scenario.get("description", ""),
                "request_id": request_id,
                "request": payload,
                "response_text": data.get("text", ""),
                "adapter_used": data.get("adapter_used"),
                "timings": data.get("timings", {}),
                "evaluation": data.get("evaluation", {}),
                "checks": [
                    {"name": check.name, "passed": check.passed, "detail": check.detail}
                    for check in checks
                ],
                "passed": all(check.passed for check in checks),
                "ttft_ms": data.get("timings", {}).get("t_brain_first_token_ms"),
                "total_ms": data.get("timings", {}).get("t_total_ms"),
            }
            manifest["results"].append(row)

    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=True), encoding="utf-8")
    _write_markdown_summary(run_dir, manifest["results"])
    print(f"Behavior evaluation written to {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
