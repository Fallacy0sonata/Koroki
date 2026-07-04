"""
Post-process DiffSinger staging clips with DeepFilterNet to remove Seed-VC buzzing artifacts.

Run with .venv_singing_v2:
    .venv_singing_v2\\Scripts\\python experiments\\diffsinger\\denoise_staging.py
"""
from __future__ import annotations

import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from scipy.signal import resample_poly
from math import gcd
from tqdm import tqdm

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STAGING_DIR = _REPO_ROOT / "data" / "diffsinger_staging" / "japanese"


def _resample(audio: np.ndarray, from_sr: int, to_sr: int) -> np.ndarray:
    if from_sr == to_sr:
        return audio
    g = gcd(from_sr, to_sr)
    return resample_poly(audio, to_sr // g, from_sr // g).astype(np.float32)


def main():
    try:
        from df.enhance import enhance, init_df
        from df.io import load_audio, save_audio
    except ImportError:
        sys.exit("Run: .venv_singing_v2\\Scripts\\pip install deepfilternet")

    wavs = sorted(_STAGING_DIR.glob("*.wav"))
    if not wavs:
        sys.exit(f"No WAV files found in {_STAGING_DIR}")

    print("Initialising DeepFilterNet model...")
    model, df_state, _ = init_df()
    model_sr = df_state.sr()  # 48000

    print(f"Denoising {len(wavs)} clips in-place...")
    errors = []
    for wav_path in tqdm(wavs):
        try:
            original, orig_sr = sf.read(str(wav_path))
            if original.ndim > 1:
                original = original.mean(axis=1)
            original = original.astype(np.float32)

            # Resample to model SR for enhancement
            audio_48k = _resample(original, orig_sr, model_sr)
            # DeepFilterNet expects a torch Tensor of shape (C, T)
            audio_48k_t = torch.from_numpy(audio_48k[None, :])

            enhanced_48k = enhance(model, df_state, audio_48k_t)
            enhanced_48k = enhanced_48k.squeeze().cpu().numpy()

            # Resample back to original SR
            enhanced = _resample(enhanced_48k, model_sr, orig_sr)

            # Clip to [-1, 1] and save in-place
            enhanced = np.clip(enhanced, -1.0, 1.0)
            sf.write(str(wav_path), enhanced, orig_sr, subtype="PCM_16")
        except Exception as e:
            errors.append((wav_path.name, str(e)))

    print(f"\nDone. {len(wavs) - len(errors)}/{len(wavs)} clips denoised.")
    if errors:
        print("Errors:")
        for name, msg in errors:
            print(f"  {name}: {msg}")


if __name__ == "__main__":
    main()
