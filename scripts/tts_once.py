"""
One-shot TTS worker.

Loads the TTS model, synthesizes a single request, writes a WAV file, and exits.
Used by the Discord bot in deferred-audio mode so Brain can respond first.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from services.tts.synthesis import TTSSynthesizer
from services.tts.voice_profiles import get_voice_profile


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Koroki one-shot TTS worker")
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--relationship-score", required=True, type=int)
    parser.add_argument("--emotion", default="neutral")
    parser.add_argument("--emotion-intensity", default=50, type=int)
    parser.add_argument("--emotion-variant", default=None)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    os.environ.setdefault("KOROKI_ROOT", str(repo_root))

    synthesizer = TTSSynthesizer()
    synthesizer.load()
    if not synthesizer.is_ready():
        return 2

    profile = get_voice_profile(args.relationship_score)
    audio_bytes = await synthesizer.synthesize(
        args.text,
        profile.name,
        args.request_id,
        emotion=args.emotion,
        emotion_intensity=args.emotion_intensity,
        emotion_variant=args.emotion_variant,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(audio_bytes)
    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
