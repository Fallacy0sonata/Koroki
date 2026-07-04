from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def _run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run filter -> score -> split pipeline for synthetic Koroki training data")
    parser.add_argument("--tasks", default="data/training/synthetic/teacher_student_tasks.jsonl")
    parser.add_argument("--accepted", default="data/training/synthetic/accepted.jsonl")
    parser.add_argument("--rejected", default="data/training/synthetic/rejected.jsonl")
    parser.add_argument("--sft-out", default="data/training/lora/synthetic_factory_sft.jsonl")
    parser.add_argument("--ranked-out", default="data/training/lora/synthetic_factory_ranked.jsonl")
    parser.add_argument("--min-chars", type=int, default=18)
    parser.add_argument("--min-score", type=int, default=24)
    parser.add_argument("--top-k", type=int, default=200)
    parser.add_argument("--replace-train-files", action="store_true")
    parser.add_argument("--run-contamination-audit", action="store_true")
    parser.add_argument("--max-meta-hits", type=int, default=0)
    parser.add_argument("--max-duplicate-ratio", type=float, default=0.12)
    parser.add_argument("--max-first-sentence-ratio", type=float, default=0.18)
    args = parser.parse_args()

    _run(
        [
            PYTHON,
            "scripts/filter_koroki_synthetic.py",
            "--in",
            args.tasks,
            "--accepted",
            args.accepted,
            "--rejected",
            args.rejected,
            "--sft-out",
            args.sft_out,
            "--min-chars",
            str(args.min_chars),
        ]
    )

    _run(
        [
            PYTHON,
            "scripts/score_koroki_samples.py",
            "--in",
            args.sft_out,
            "--out",
            args.ranked_out,
            "--min-score",
            str(args.min_score),
            "--top-k",
            str(args.top_k),
        ]
    )

    split_cmd = [
        PYTHON,
        "scripts/split_ranked_sft_by_tier.py",
        "--in",
        args.ranked_out,
    ]
    if args.replace_train_files:
        split_cmd.append("--replace-train-files")
    _run(split_cmd)

    if args.run_contamination_audit:
        _run(
            [
                PYTHON,
                "scripts/audit_training_contamination.py",
                "--in",
                args.ranked_out,
                "--max-meta-hits",
                str(args.max_meta_hits),
                "--max-duplicate-ratio",
                str(args.max_duplicate_ratio),
                "--max-first-sentence-ratio",
                str(args.max_first_sentence_ratio),
            ]
        )

    print("Pipeline complete.")


if __name__ == "__main__":
    main()
