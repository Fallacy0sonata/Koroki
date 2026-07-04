"""
Prepare singing clips for MFA forced alignment.

Reads data/singing_training/clips/ (wav + lab pairs), then:
  - Resamples audio to 16 kHz 16-bit PCM (MFA requirement)
  - Detects language from lab text (Japanese unicode vs ASCII)
  - Cleans English labels (lowercase, strip punctuation)
  - Writes to data/mfa_input/english/ and data/mfa_input/japanese/

Run:
    python experiments/diffsinger/prepare_mfa.py
"""

from __future__ import annotations

import re
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

TARGET_SR = 16000
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _is_japanese(text: str) -> bool:
    for ch in text:
        cp = ord(ch)
        # Hiragana, Katakana, CJK Unified Ideographs, half-width Katakana
        if 0x3040 <= cp <= 0x9FFF or 0xFF65 <= cp <= 0xFF9F:
            return True
    return False


def _clean_english(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s']", " ", text)   # keep apostrophes
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    if orig_sr == target_sr:
        return audio
    g = gcd(orig_sr, target_sr)
    up, down = target_sr // g, orig_sr // g
    if audio.ndim == 1:
        return resample_poly(audio, up, down).astype(np.float32)
    return np.stack(
        [resample_poly(audio[:, c], up, down) for c in range(audio.shape[1])], axis=1
    ).astype(np.float32)


def main() -> None:
    clips_dir = _REPO_ROOT / "data" / "singing_training" / "clips"
    out_root = _REPO_ROOT / "data" / "mfa_input"

    en_dir = out_root / "english"
    ja_dir = out_root / "japanese"
    en_dir.mkdir(parents=True, exist_ok=True)
    ja_dir.mkdir(parents=True, exist_ok=True)

    wavs = sorted(clips_dir.glob("*.wav"), key=lambda p: p.stem)
    en_count = ja_count = skip_count = 0

    for wav_path in wavs:
        lab_path = wav_path.with_suffix(".lab")
        if not lab_path.exists():
            skip_count += 1
            continue

        text = lab_path.read_text(encoding="utf-8").strip()
        if not text:
            skip_count += 1
            continue

        is_jp = _is_japanese(text)
        target_dir = ja_dir if is_jp else en_dir

        # Load, mono-mix, resample
        audio, sr = sf.read(str(wav_path))
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        audio = audio.astype(np.float32)
        resampled = _resample(audio, sr, TARGET_SR)

        sf.write(str(target_dir / wav_path.name), resampled, TARGET_SR, subtype="PCM_16")

        cleaned = text if is_jp else _clean_english(text)
        (target_dir / lab_path.name).write_text(cleaned, encoding="utf-8")

        if is_jp:
            ja_count += 1
        else:
            en_count += 1

        if (en_count + ja_count) % 50 == 0:
            print(f"  {en_count + ja_count}/{len(wavs)} processed...")

    print(f"\nDone.")
    print(f"  English : {en_count} clips  →  {en_dir}")
    print(f"  Japanese: {ja_count} clips  →  {ja_dir}")
    print(f"  Skipped : {skip_count}  (empty labels)")


if __name__ == "__main__":
    main()
