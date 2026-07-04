"""
Singing pipeline v4 — transposition, not SVC.

Premise (validated by data/voice_analysis/koroki_vs_ikura.png):
  Koroki and Ikura's voice fingerprints overlap with Ikura's own variance
  (cosine 0.796 centroid, vs Ikura's 0.795 self-similarity). The F0 gap is
  +4.3 semitones (Ikura singing vs Koroki speech), but most of that gap is
  the speech-vs-singing mode mismatch — the actual singing-register gap is
  likely 1-2 semitones.

Pipeline:
  song name → yt-dlp → demucs (htdemucs two-stems) → pitch-shift vocals
  by N semitones → mix vocals + instrumental → output

This bypasses the SVC quality ceiling entirely — no model training, no voice
conversion, just YOASOBI's actual singing transposed into Koroki's natural
register. The voice identity is already there per the measurement.

Run from Koroki root:
    .venv_singing\\Scripts\\python.exe experiments\\singing-transpose\\sing_song.py \\
        "yoasobi idol" --semitones -2.0

Args:
    song_query: search query (yt-dlp searches YouTube)
    --semitones FLOAT: pitch shift to apply to vocals (default -2.0)
    --vocal-gain FLOAT: vocal mix level (default 1.0)
    --redo: force redo all stages (delete cached work dir)
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parents[2]
WORK_ROOT = REPO_ROOT / "data" / "transpose_work"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("transpose")

# yt-dlp resolution (same pattern as singing v1 adapter)
_YTDLP = str(Path(sys.executable).parent / "yt-dlp.exe")
if not Path(_YTDLP).exists():
    _YTDLP = str(Path(sys.executable).parent / "yt-dlp")
if not Path(_YTDLP).exists():
    _YTDLP = "yt-dlp"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", text.lower()).strip("_")
    return s[:60] or "song"


# ── Pipeline stages ──────────────────────────────────────────────────────

def download_song(query: str, work_dir: Path) -> Path:
    """yt-dlp download → returns path to source WAV."""
    cached = work_dir / "source.wav"
    if cached.exists():
        logger.info("source cached: %s", cached)
        return cached
    out_template = str(work_dir / "source.%(ext)s")
    cmd = [
        _YTDLP, "--no-playlist",
        "--extract-audio", "--audio-format", "wav", "--audio-quality", "0",
        "--output", out_template, "--default-search", "ytsearch",
        "--no-warnings",
        "--no-check-certificates",  # Windows certstore mismatch workaround
        f"ytsearch1:{query}",
    ]
    logger.info("yt-dlp: searching '%s'", query)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr[:500]}")
    if cached.exists():
        return cached
    for p in work_dir.glob("source.*"):
        return p
    raise FileNotFoundError("yt-dlp produced no output file")


def separate_vocals(source_path: Path, work_dir: Path) -> tuple[Path, Path]:
    """demucs htdemucs two-stems → (vocals.wav, no_vocals.wav)."""
    stems_dir = work_dir / "stems"
    vocals = stems_dir / "vocals.wav"
    no_vocals = stems_dir / "no_vocals.wav"
    if vocals.exists() and no_vocals.exists():
        logger.info("stems cached: %s", stems_dir)
        return vocals, no_vocals

    stems_dir.mkdir(exist_ok=True)
    out_dir = work_dir / "demucs_out"
    cmd = [
        sys.executable, "-m", "demucs",
        "--name", "htdemucs",
        "--two-stems", "vocals",
        "--out", str(out_dir),
        str(source_path),
    ]
    logger.info("demucs: separating vocals from %s", source_path.name)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(
            f"demucs failed (rc={result.returncode}):\n"
            f"STDERR: {result.stderr[-1000:]}"
        )
    base = out_dir / "htdemucs" / source_path.stem
    shutil.copy(base / "vocals.wav", vocals)
    shutil.copy(base / "no_vocals.wav", no_vocals)
    return vocals, no_vocals


def pitch_shift_vocals(vocals_path: Path, work_dir: Path, semitones: float) -> Path:
    """Pitch-shift vocals by N semitones using librosa phase vocoder.

    For small shifts (|n| <= 3), librosa's phase vocoder produces clean output
    without noticeable formant artifacts. Above that, consider upgrading to
    pedalboard.PitchShift for formant preservation.
    """
    out = work_dir / f"vocals_shifted_{semitones:+.1f}st.wav"
    if out.exists():
        logger.info("shifted vocals cached: %s", out)
        return out

    logger.info("pitch-shift: vocals %+0.2f semitones", semitones)
    y, sr = librosa.load(str(vocals_path), sr=None, mono=False)
    # librosa.pitch_shift wants mono — process channels separately if stereo
    if y.ndim == 1:
        y_shifted = librosa.effects.pitch_shift(y=y, sr=sr, n_steps=semitones)
    else:
        channels = [
            librosa.effects.pitch_shift(y=y[ch], sr=sr, n_steps=semitones)
            for ch in range(y.shape[0])
        ]
        y_shifted = np.stack(channels)

    sf.write(str(out), y_shifted.T if y_shifted.ndim > 1 else y_shifted, sr)
    return out


def mix_tracks(vocals_path: Path, instrumental_path: Path, output_path: Path,
                vocal_gain: float = 1.0) -> Path:
    """Mix vocals + instrumental into final track."""
    logger.info("mixing vocals (gain=%.2f) + instrumental", vocal_gain)
    v, sr_v = librosa.load(str(vocals_path), sr=None, mono=False)
    i, sr_i = librosa.load(str(instrumental_path), sr=None, mono=False)

    if sr_v != sr_i:
        logger.warning("sample-rate mismatch: vocals=%d instrumental=%d — resampling", sr_v, sr_i)
        v = librosa.resample(v, orig_sr=sr_v, target_sr=sr_i)
        sr_v = sr_i

    # Pad to same length
    if v.ndim == 1: v = v[None, :]
    if i.ndim == 1: i = i[None, :]
    target_len = min(v.shape[-1], i.shape[-1])
    v = v[:, :target_len]
    i = i[:, :target_len]

    # Match channel count
    if v.shape[0] != i.shape[0]:
        if v.shape[0] == 1:
            v = np.repeat(v, i.shape[0], axis=0)
        elif i.shape[0] == 1:
            i = np.repeat(i, v.shape[0], axis=0)

    mix = (v * vocal_gain) + i
    # Soft clip via tanh — gentler than hard normalization
    peak = float(np.max(np.abs(mix)))
    if peak > 1.0:
        logger.info("mix peak %.2f — normalizing", peak)
        mix = mix / peak * 0.97

    sf.write(str(output_path), mix.T if mix.shape[0] > 1 else mix[0], sr_i)
    logger.info("output: %s", output_path)
    return output_path


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("song", help="search query (yt-dlp searches YouTube)")
    parser.add_argument("--semitones", type=float, default=-2.0,
                         help="pitch shift to apply to vocals (default -2.0)")
    parser.add_argument("--vocal-gain", type=float, default=1.0,
                         help="vocal mix level (default 1.0)")
    parser.add_argument("--redo", action="store_true",
                         help="delete cached work dir and redo all stages")
    args = parser.parse_args()

    slug = slugify(args.song)
    work_dir = WORK_ROOT / slug
    if args.redo and work_dir.exists():
        logger.info("removing cached work dir: %s", work_dir)
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 70)
    logger.info("Singing v4 (transposition) — %s", args.song)
    logger.info("shift=%+.2f semitones  vocal_gain=%.2f  work_dir=%s",
                 args.semitones, args.vocal_gain, work_dir)
    logger.info("=" * 70)

    source = download_song(args.song, work_dir)
    vocals, instrumental = separate_vocals(source, work_dir)
    shifted_vocals = pitch_shift_vocals(vocals, work_dir, args.semitones)
    output = work_dir / f"output_{args.semitones:+.1f}st.wav"
    mix_tracks(shifted_vocals, instrumental, output, args.vocal_gain)

    logger.info("=" * 70)
    logger.info("DONE: %s", output)
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
