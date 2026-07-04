from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


VALID_TIERS = ("owner", "tsundere", "peasant")


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _tier_of(row: dict) -> str:
    meta = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
    tier = str(meta.get("tier", "tsundere")).strip().lower()
    if tier not in VALID_TIERS:
        return "tsundere"
    return tier


def main() -> None:
    parser = argparse.ArgumentParser(description="Split ranked synthetic SFT JSONL into owner/tsundere/peasant files")
    parser.add_argument("--in", dest="in_path", default="data/training/lora/synthetic_factory_ranked.jsonl")
    parser.add_argument("--out-dir", default="data/training/lora")
    parser.add_argument("--prefix", default="synthetic_factory")
    parser.add_argument(
        "--replace-train-files",
        action="store_true",
        help="Also overwrite owner_sft.jsonl, tsundere_sft.jsonl, peasant_sft.jsonl for direct training",
    )
    args = parser.parse_args()

    in_path = Path(args.in_path)
    if not in_path.exists():
        raise FileNotFoundError(f"Input file not found: {in_path}")

    rows = _read_jsonl(in_path)
    by_tier: dict[str, list[dict]] = {tier: [] for tier in VALID_TIERS}
    for row in rows:
        by_tier[_tier_of(row)].append(row)

    out_dir = Path(args.out_dir)
    out_paths: dict[str, Path] = {}
    counts = Counter()

    for tier in VALID_TIERS:
        out_path = out_dir / f"{args.prefix}_{tier}_sft.jsonl"
        _write_jsonl(out_path, by_tier[tier])
        out_paths[tier] = out_path
        counts[tier] = len(by_tier[tier])

    replaced: dict[str, str] = {}
    if args.replace_train_files:
        for tier in VALID_TIERS:
            train_path = out_dir / f"{tier}_sft.jsonl"
            _write_jsonl(train_path, by_tier[tier])
            replaced[tier] = train_path.as_posix()

    print(
        json.dumps(
            {
                "input_rows": len(rows),
                "tier_counts": dict(counts),
                "outputs": {tier: path.as_posix() for tier, path in out_paths.items()},
                "replaced_train_files": replaced,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
