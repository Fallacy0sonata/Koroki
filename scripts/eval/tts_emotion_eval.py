from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.utils.config import get_settings  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Koroki emotional TTS comparison matrix")
    parser.add_argument("--base-url", default=None, help="TTS base URL, defaults to settings")
    parser.add_argument("--output-dir", default=None, help="Override output root directory")
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args()


def _timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _relationship_score_for_profile(profile_name: str, settings: dict[str, Any]) -> int:
    voice_profiles = settings.get("voice_profiles", {})
    sultry_threshold = int(voice_profiles.get("sultry_sexy_flirty", {}).get("min_relationship_score", 50))
    if profile_name == "sultry_sexy_flirty":
        return sultry_threshold
    return max(0, sultry_threshold - 1)


def _write_rubric(run_dir: Path, manifest: dict[str, Any]) -> None:
    lines = [
        "# Koroki TTS Emotion Review",
        "",
        "Review each sample while comparing against the neutral version of the same line.",
        "",
        "## Human Rubric",
        "",
        "- Is the requested emotion perceptible?",
        "- Is it distinguishable from neutral?",
        "- Is the difference consistent across sentence types?",
        "- Is the perceived effect mostly carried by clone identity instead of emotion conditioning?",
        "",
        "| File | Sentence Set | Emotion | Intensity | Variant | Perceptible | Distinct From Neutral | Consistent | Clone Carrying System? | Notes |",
        "|---|---|---|---:|---|---|---|---|---|---|",
    ]
    for sample in manifest["samples"]:
        lines.append(
            f"| {sample['file_name']} | {sample['sentence_set']} | {sample['emotion']} | "
            f"{sample['intensity']} | {sample['variant']} | [ ] | [ ] | [ ] | [ ] | |"
        )
    (run_dir / "review.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = _parse_args()
    settings = get_settings()
    eval_cfg = settings.get("evaluation", {}).get("tts", {})
    output_root = Path(args.output_dir or settings.get("evaluation", {}).get("output_dir", "data/evaluations"))
    tts_url = args.base_url or settings.get("services", {}).get("tts", {}).get("url", "http://127.0.0.1:9880")

    run_dir = ROOT / output_root / "tts" / _timestamp_slug()
    run_dir.mkdir(parents=True, exist_ok=True)

    profile_name = str(eval_cfg.get("profile", "sassy_regal"))
    relationship_score = int(eval_cfg.get("relationship_score", _relationship_score_for_profile(profile_name, settings)))

    manifest: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "tts_url": tts_url,
        "profile_name": profile_name,
        "relationship_score": relationship_score,
        "samples": [],
    }

    with httpx.Client(timeout=args.timeout) as client:
        for sentence_set_name, lines in dict(eval_cfg.get("sentence_sets", {})).items():
            for line_index, source_line in enumerate(lines, start=1):
                for dimension in list(eval_cfg.get("dimensions", [])):
                    request_id = f"tts_eval_{sentence_set_name}_{line_index}_{dimension['emotion']}_{_timestamp_slug()}"
                    payload = {
                        "request_id": request_id,
                        "text": source_line,
                        "relationship_score": relationship_score,
                        "emotion": dimension["emotion"],
                        "emotion_intensity": int(dimension["intensity"]),
                        "emotion_variant": dimension["variant"],
                    }
                    response = client.post(f"{tts_url}/v1/synthesize", json=payload)
                    response.raise_for_status()

                    file_name = (
                        f"{sentence_set_name}_{line_index:02d}_"
                        f"{dimension['emotion']}_{int(dimension['intensity']):02d}.wav"
                    )
                    output_path = run_dir / file_name
                    output_path.write_bytes(response.content)

                    manifest["samples"].append(
                        {
                            "request_id": request_id,
                            "sentence_set": sentence_set_name,
                            "source_line": source_line,
                            "line_index": line_index,
                            "profile_name": profile_name,
                            "relationship_score": relationship_score,
                            "emotion": dimension["emotion"],
                            "intensity": int(dimension["intensity"]),
                            "variant": dimension["variant"],
                            "file_name": file_name,
                            "output_path": str(output_path),
                            "content_bytes": len(response.content),
                        }
                    )

    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=True), encoding="utf-8")
    _write_rubric(run_dir, manifest)
    print(f"TTS emotion evaluation written to {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
