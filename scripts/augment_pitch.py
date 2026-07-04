"""
Pitch augmentation for RVC training data.

Takes EN_sample.wav (highest quality source) and generates pitch-shifted
copies at multiple semitone offsets. This teaches RVC what Koroki's voice
sounds like across the singing pitch range without needing actual singing.

Uses librosa phase-vocoder pitch shifting — changes pitch while preserving
duration, so the voice sounds higher/lower like a human, not chipmunk/slowed.

Usage:
    .venv_singing\Scripts\python scripts\augment_pitch.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parents[1]
VOICE_SAMPLES = REPO_ROOT / "voice_samples"
OUT_DIR = VOICE_SAMPLES / "rvc_training"
APPLIO_DATASET = REPO_ROOT / "ApplioV3.6.2" / "assets" / "datasets" / "Koroki_v2"

# Semitone offsets to generate. Covers typical singing range above and below speech.
SEMITONE_OFFSETS = [-9, -6, -3, 3, 6, 9]

AUGMENT_SOURCES = [
    VOICE_SAMPLES / "EN_sample.wav",
]


def pitch_shift(audio: np.ndarray, sr: int, semitones: int) -> np.ndarray:
    """Shift pitch using librosa phase vocoder — preserves duration."""
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    shifted = librosa.effects.pitch_shift(audio.astype(np.float32), sr=sr, n_steps=semitones)
    return shifted


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Delete old bad resampling-based augmented files first
    deleted = 0
    for f in OUT_DIR.glob("*_pitch_*.wav"):
        f.unlink()
        deleted += 1
    if deleted:
        print(f"Deleted {deleted} old resampling-based pitch files")

    total_written = 0

    for src_path in AUGMENT_SOURCES:
        if not src_path.exists():
            print(f"WARNING: {src_path.name} not found, skipping")
            continue

        audio, sr = librosa.load(str(src_path), sr=None, mono=True)
        print(f"\n{src_path.name} — {len(audio)/sr:.1f}s @ {sr}Hz")

        for semitones in SEMITONE_OFFSETS:
            sign = "p" if semitones > 0 else "m"
            label = f"{src_path.stem}_pitch_{sign}{abs(semitones)}.wav"
            out_path = OUT_DIR / label

            if out_path.exists():
                print(f"  SKIP (exists): {label}")
                total_written += 1
                continue

            shifted = pitch_shift(audio, sr, semitones)
            sf.write(str(out_path), shifted, sr, subtype="PCM_16")
            direction = "up" if semitones > 0 else "down"
            print(f"  {semitones:+d} semitones ({direction}) -> {label}")
            total_written += 1

    print(f"\nTotal augmented files: {total_written}")

    # Sync to Applio dataset
    if APPLIO_DATASET.exists():
        # Remove old bad files from dataset too
        for f in APPLIO_DATASET.glob("*_pitch_*.wav"):
            f.unlink()

        print(f"\nSyncing to Applio dataset: {APPLIO_DATASET}")
        synced = 0
        for f in OUT_DIR.glob("*_pitch_*.wav"):
            dest = APPLIO_DATASET / f.name
            shutil.copy2(str(f), str(dest))
            synced += 1
        print(f"Copied {synced} files to Applio dataset")

    print("""
=== Next: Process data in Applio, then Train ===
""")


if __name__ == "__main__":
    main()
