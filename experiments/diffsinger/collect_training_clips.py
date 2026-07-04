"""
Collect high-quality DiffSinger training clips from JPop/anime YouTube songs.

Pipeline per song:
  yt-dlp download → BS-Roformer (vocals) → split into 5-15s segments
  → Seed-VC ×N per segment → pick best take (MFCC speaker similarity to Koroki)
  → Whisper transcription → digit normalization → save WAV + .lab

Output:  data/diffsinger_staging/japanese/
         (WAV + .lab pairs, ready to import into singing_training/clips/)

Run with .venv_singing_v2 (needs faster-whisper installed):
    .venv_singing_v2\\Scripts\\pip install faster-whisper        # one-time
    .venv_singing_v2\\Scripts\\python.exe experiments\\diffsinger\\collect_training_clips.py
    .venv_singing_v2\\Scripts\\python.exe experiments\\diffsinger\\collect_training_clips.py --runs 5 --limit 400

After collecting, import and retrain:
    xcopy data\\diffsinger_staging\\japanese\\*.wav data\\singing_training\\clips\\ /Y
    xcopy data\\diffsinger_staging\\japanese\\*.lab data\\singing_training\\clips\\ /Y
    .venv\\Scripts\\python.exe experiments\\diffsinger\\prepare_mfa.py
    # run MFA
    .venv\\Scripts\\python.exe experiments\\diffsinger\\build_dataset.py
    # binarize + train
"""

from __future__ import annotations

import os
# Must be set before any OMP-using library (numpy, torch, onnxruntime) is imported.
# Windows often has two OMP runtimes (Intel libiomp5 + LLVM libomp) loaded simultaneously.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import argparse
import csv
import re
import shutil
import subprocess
import sys
import tempfile
from math import gcd
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEED_VC_RUNNER = _REPO_ROOT / "experiments" / "singing-v2" / "seed_vc_runner.py"
_BS_ROFORMER_RUNNER = Path(__file__).parent / "bs_roformer_runner.py"
_DIFFSINGER_PYTHON = _REPO_ROOT / ".venv_diffsinger" / "Scripts" / "python.exe"
_VOICE_SAMPLES_DIR = _REPO_ROOT / "voice_samples"
_STAGING_DIR = _REPO_ROOT / "data" / "diffsinger_staging" / "japanese"
_DEFAULT_SONG_LIST = Path(__file__).parent / "jpop_song_list.txt"

_MIN_SEG_DUR = 5.0      # seconds — shorter clips are useless for training
_MAX_SEG_DUR = 15.0     # seconds — DiffSinger segments shouldn't be too long
_SILENCE_TOP_DB = 35    # librosa split sensitivity (higher = more aggressive)
_TARGET_SR = 44100      # DiffSinger vocoder sample rate

_WHISPER_LANG = "ja"
_MIN_TRANSCRIPT_CHARS = 3
_MIN_LOGPROB = -1.2     # Whisper confidence floor; below this = unreliable transcript

# Digit/counter normalization — same table as sing_song.py
_COUNTER_TSU = {
    '1': 'ひとつ', '2': 'ふたつ', '3': 'みっつ', '4': 'よっつ', '5': 'いつつ',
    '6': 'むっつ', '7': 'ななつ', '8': 'やっつ', '9': 'ここのつ', '10': 'とお',
}
_DIGITS_JA = {
    '0': 'ゼロ', '1': 'いち', '2': 'に', '3': 'さん', '4': 'よん',
    '5': 'ご', '6': 'ろく', '7': 'なな', '8': 'はち', '9': 'きゅう',
}
_MULTI_JA = [('1000', 'せん'), ('100', 'ひゃく'), ('10', 'じゅう')]


def _normalize_transcript(text: str) -> str:
    text = re.sub(r'(\d)つ', lambda m: _COUNTER_TSU.get(m.group(1), m.group(0)), text)
    for digits, kana in _MULTI_JA:
        text = text.replace(digits, kana)
    for d, k in _DIGITS_JA.items():
        text = text.replace(d, k)
    return text.strip()


def _find_yt_dlp() -> str:
    for candidate in [
        str(Path(sys.executable).parent / "yt-dlp.exe"),
        str(Path(sys.executable).parent / "yt-dlp"),
        "yt-dlp",
    ]:
        if Path(candidate).exists() or shutil.which(candidate):
            return candidate
    return "yt-dlp"


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def _download(query: str, work_dir: str, ytdlp: str) -> str:
    out_template = os.path.join(work_dir, "source.%(ext)s")
    cmd = [
        ytdlp, "--no-playlist", "--extract-audio", "--audio-format", "wav",
        "--audio-quality", "0", "--output", out_template,
        "--default-search", "ytsearch", "--no-warnings",
        f"ytsearch1:{query}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr[:400]}")
    wav = os.path.join(work_dir, "source.wav")
    if os.path.exists(wav):
        return wav
    for p in Path(work_dir).glob("source.*"):
        return str(p)
    raise FileNotFoundError("yt-dlp produced no output")


def _separate(source_path: str, work_dir: str) -> str:
    """Separate vocals using BS-Roformer via .venv_diffsinger python."""
    out_dir = os.path.join(work_dir, "bs_roformer_out")
    os.makedirs(out_dir, exist_ok=True)
    result = subprocess.run(
        [str(_DIFFSINGER_PYTHON), str(_BS_ROFORMER_RUNNER), source_path, "--output-dir", out_dir],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"BS-Roformer failed:\n{result.stderr[-600:]}")
    vocals_path = result.stdout.strip().splitlines()[-1]
    if not Path(vocals_path).exists():
        raise FileNotFoundError(f"BS-Roformer vocals not found: {vocals_path}")
    return vocals_path


def _split_segments(
    mono: np.ndarray, sr: int, min_dur: float, max_dur: float,
) -> list[tuple[int, int]]:
    """Split mono audio into phrase-length segments avoiding mid-word cuts."""
    import librosa

    intervals = librosa.effects.split(mono, top_db=_SILENCE_TOP_DB, frame_length=2048, hop_length=512)

    # Merge intervals separated by < 0.3s (breath gaps within a phrase)
    merged: list[list[int]] = []
    gap = int(0.3 * sr)
    for s, e in intervals:
        if merged and s - merged[-1][1] < gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])

    min_s = int(min_dur * sr)
    max_s = int(max_dur * sr)
    result: list[tuple[int, int]] = []

    for s, e in merged:
        dur = e - s
        if dur < min_s:
            continue
        if dur <= max_s:
            result.append((s, e))
            continue
        # Long phrase: find quietest mid-point to split
        seg = mono[s:e]
        inner = librosa.effects.split(seg, top_db=_SILENCE_TOP_DB - 5, frame_length=2048, hop_length=512)
        mid = dur // 2
        best_split = mid
        best_dist = dur
        for si, ei in inner:
            for c in (si, ei):
                if min_s < c < dur - min_s:
                    d = abs(c - mid)
                    if d < best_dist:
                        best_dist = d
                        best_split = c
        if best_split >= min_s:
            result.append((s, s + best_split))
        if (e - s - best_split) >= min_s:
            result.append((s + best_split, e))

    return result


def _build_reference_audio() -> str:
    ref_path = str(_VOICE_SAMPLES_DIR / "_seed_vc_reference.wav")
    if Path(ref_path).exists():
        return ref_path
    clips = sorted(_VOICE_SAMPLES_DIR.glob("*.wav"))
    if not clips:
        raise FileNotFoundError(
            f"No voice samples in {_VOICE_SAMPLES_DIR}. "
            "Start the Seed-VC adapter once to build the reference audio."
        )
    parts, target_sr = [], None
    for p in clips:
        a, sr = sf.read(str(p))
        if target_sr is None:
            target_sr = sr
        if a.ndim > 1:
            a = a.mean(axis=1)
        parts.append(a.astype(np.float32))
    combined = np.concatenate(parts)
    sf.write(ref_path, combined, target_sr, subtype="PCM_16")
    print(f"Built reference: {ref_path} ({len(combined)/target_sr:.1f}s)")
    return ref_path


def _estimate_median_f0(audio: np.ndarray, sr: int) -> Optional[float]:
    mono = (audio.mean(axis=1) if audio.ndim > 1 else audio).astype(np.float64)
    try:
        import librosa
        f0, voiced, _ = librosa.pyin(mono, fmin=80.0, fmax=800.0, sr=sr)
        v = f0[voiced & ~np.isnan(f0)]
        if len(v) >= 3:
            return float(np.median(v))
    except Exception:
        pass
    return None


def _run_seed_vc_once(
    seg_wav: str, reference_audio: str, ref_f0_hz: Optional[float],
    out_dir: str, diffusion_steps: int,
) -> str:
    transpose = 0
    if ref_f0_hz:
        try:
            a, sr = sf.read(seg_wav)
            src_f0 = _estimate_median_f0(a, sr)
            if src_f0:
                shift = int(round(12.0 * np.log2(ref_f0_hz / src_f0)))
                shift = max(-24, min(24, shift))
                if abs(shift) > 2:
                    transpose = shift
        except Exception:
            pass

    result = subprocess.run(
        [sys.executable, str(_SEED_VC_RUNNER),
         "--source", seg_wav,
         "--target", reference_audio,
         "--output", out_dir,
         "--diffusion-steps", str(diffusion_steps),
         "--f0-condition", "True",
         "--semi-tone-shift", str(transpose),
         "--inference-cfg-rate", "0.7",
         "--fp16", "True"],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Seed-VC: {result.stderr[-300:]}")
    outputs = list(Path(out_dir).glob("*.wav"))
    if not outputs:
        raise FileNotFoundError("Seed-VC produced no output")
    return str(outputs[0])


def _mfcc_similarity(a1: np.ndarray, sr1: int, a2: np.ndarray, sr2: int) -> float:
    """Cosine similarity of mean MFCC vectors — higher = more like Koroki."""
    import librosa
    m1 = librosa.feature.mfcc(y=a1.astype(np.float64), sr=sr1, n_mfcc=40).mean(axis=1)
    m2 = librosa.feature.mfcc(y=a2.astype(np.float64), sr=sr2, n_mfcc=40).mean(axis=1)
    return float(np.dot(m1, m2) / (np.linalg.norm(m1) * np.linalg.norm(m2) + 1e-8))


def _best_seed_vc_take(
    seg_wav: str,
    reference_audio: str,
    ref_mono: np.ndarray,
    ref_sr: int,
    ref_f0_hz: Optional[float],
    n_runs: int,
    work_dir: str,
    diffusion_steps: int,
) -> tuple[str, float]:
    best_path: Optional[str] = None
    best_score = -1.0

    for i in range(n_runs):
        run_dir = os.path.join(work_dir, f"run_{i}")
        os.makedirs(run_dir, exist_ok=True)
        try:
            out = _run_seed_vc_once(seg_wav, reference_audio, ref_f0_hz, run_dir, diffusion_steps)
            a, sr = sf.read(out)
            if a.ndim > 1:
                a = a.mean(axis=1)
            score = _mfcc_similarity(a.astype(np.float32), sr, ref_mono, ref_sr)
            if score > best_score:
                best_score = score
                best_path = out
        except Exception as exc:
            print(f"    run {i+1} failed: {exc}")

    if best_path is None:
        raise RuntimeError("All Seed-VC runs failed")
    return best_path, best_score


def _resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    if orig_sr == target_sr:
        return audio
    from scipy.signal import resample_poly
    g = gcd(orig_sr, target_sr)
    return resample_poly(audio, target_sr // g, orig_sr // g).astype(np.float32)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--songs", default=str(_DEFAULT_SONG_LIST),
                        help="Text file with song names, one per line")
    parser.add_argument("--runs", type=int, default=3,
                        help="Seed-VC runs per segment — pick the best take")
    parser.add_argument("--diffusion-steps", type=int, default=10)
    parser.add_argument("--limit", type=int, default=400,
                        help="Stop after this many clips total")
    parser.add_argument("--min-sim", type=float, default=0.80,
                        help="Min MFCC similarity to Koroki reference (0-1)")
    parser.add_argument("--whisper-model", default="medium")
    parser.add_argument("--out", default=str(_STAGING_DIR))
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ytdlp = _find_yt_dlp()

    songs_path = Path(args.songs)
    if not songs_path.exists():
        print(f"Song list not found: {songs_path}")
        print("Create it at experiments/diffsinger/jpop_song_list.txt (one song per line).")
        return

    songs = [
        ln.strip() for ln in songs_path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    if not songs:
        print("No songs in list.")
        return

    print(f"Songs: {len(songs)}  |  Runs/seg: {args.runs}  |  Steps: {args.diffusion_steps}  |  Limit: {args.limit}")
    print(f"Output: {out_dir}")

    # Estimated runtime hint
    approx_min = len(songs) * 15 * args.runs * 1.5 / 60
    print(f"Estimated runtime: {approx_min:.0f}–{approx_min*1.5:.0f} min (GPU dependent)\n")

    reference_audio = _build_reference_audio()
    ref_audio, ref_sr = sf.read(reference_audio)
    ref_mono = (ref_audio.mean(axis=1) if ref_audio.ndim > 1 else ref_audio).astype(np.float32)
    ref_f0_hz = _estimate_median_f0(ref_audio, ref_sr)
    if ref_f0_hz:
        print(f"Reference F0: {ref_f0_hz:.1f} Hz")

    print(f"Loading faster-whisper ({args.whisper_model})...")
    try:
        from faster_whisper import WhisperModel
        from huggingface_hub import snapshot_download

        # Download to a local dir with symlinks disabled — Windows symlink
        # creation requires Developer Mode which most users don't have enabled.
        model_dir = _REPO_ROOT / "checkpoints" / f"faster-whisper-{args.whisper_model}"
        if not (model_dir / "model.bin").exists():
            print(f"  Downloading model to {model_dir} ...")
            snapshot_download(
                repo_id=f"Systran/faster-whisper-{args.whisper_model}",
                local_dir=str(model_dir),
                local_dir_use_symlinks=False,
            )
        whisper = WhisperModel(str(model_dir), device="cuda", compute_type="float16")
    except ImportError:
        print("\nfaster-whisper not installed. Run:")
        print("  .venv_singing_v2\\Scripts\\pip install faster-whisper")
        return

    manifest_rows: list[dict] = []
    clip_counter = 0
    work_root = tempfile.mkdtemp(prefix="koroki_collect_")

    try:
        for song_idx, query in enumerate(songs):
            if clip_counter >= args.limit:
                print(f"\nReached clip limit ({args.limit}). Stopping.")
                break

            print(f"\n[{song_idx+1}/{len(songs)}] {query}")
            song_work = os.path.join(work_root, f"song_{song_idx:04d}")
            os.makedirs(song_work, exist_ok=True)

            try:
                print("  Downloading...")
                source = _download(query, song_work, ytdlp)

                print("  Separating vocals (BS-Roformer)...")
                vocals = _separate(source, song_work)

                vocals_audio, vocals_sr = sf.read(vocals)
                vocals_mono = (
                    vocals_audio.mean(axis=1) if vocals_audio.ndim > 1 else vocals_audio
                ).astype(np.float32)

                segments = _split_segments(vocals_mono, vocals_sr, _MIN_SEG_DUR, _MAX_SEG_DUR)
                print(f"  Segments: {len(segments)}")

                song_clips = 0

                for seg_idx, (seg_s, seg_e) in enumerate(segments):
                    if clip_counter >= args.limit:
                        break

                    seg_dur = (seg_e - seg_s) / vocals_sr
                    seg_audio = vocals_mono[seg_s:seg_e]

                    seg_wav = os.path.join(song_work, f"seg_{seg_idx:04d}.wav")
                    sf.write(seg_wav, seg_audio, vocals_sr, subtype="PCM_16")

                    print(f"  Seg {seg_idx+1}/{len(segments)} ({seg_dur:.1f}s): Seed-VC ×{args.runs}...", end=" ", flush=True)

                    try:
                        vc_work = os.path.join(song_work, f"vc_{seg_idx:04d}")
                        os.makedirs(vc_work, exist_ok=True)

                        best_wav, sim = _best_seed_vc_take(
                            seg_wav, reference_audio, ref_mono, ref_sr,
                            ref_f0_hz, args.runs, vc_work, args.diffusion_steps,
                        )

                        print(f"sim={sim:.3f}", end=" ", flush=True)

                        if sim < args.min_sim:
                            print(f"→ SKIP (min {args.min_sim})")
                            continue

                        # Transcribe original segment (original singer's voice → accurate Japanese text)
                        segs, _ = whisper.transcribe(
                            seg_wav, language=_WHISPER_LANG, beam_size=5,
                            word_timestamps=False, vad_filter=True,
                        )
                        seg_list = list(segs)
                        text = "".join(s.text for s in seg_list).strip()
                        logprob = float(np.mean([s.avg_logprob for s in seg_list])) if seg_list else -99.0
                        text = _normalize_transcript(text)

                        print(f"| lp={logprob:.2f} | '{text[:35]}'")

                        if len(text) < _MIN_TRANSCRIPT_CHARS:
                            print("    SKIP: transcript too short")
                            continue
                        if logprob < _MIN_LOGPROB:
                            print("    SKIP: low Whisper confidence")
                            continue

                        # Save converted audio at 44.1kHz
                        best_audio, best_sr = sf.read(best_wav)
                        if best_audio.ndim > 1:
                            best_audio = best_audio.mean(axis=1)
                        if best_sr != _TARGET_SR:
                            best_audio = _resample(best_audio.astype(np.float32), best_sr, _TARGET_SR)
                            best_sr = _TARGET_SR

                        clip_id = f"vc_{clip_counter:05d}"
                        sf.write(str(out_dir / f"{clip_id}.wav"), best_audio, best_sr, subtype="PCM_16")
                        (out_dir / f"{clip_id}.lab").write_text(text, encoding="utf-8")

                        manifest_rows.append({
                            "name": clip_id,
                            "source": query,
                            "seg_start_s": round(seg_s / vocals_sr, 3),
                            "seg_dur_s": round(seg_dur, 3),
                            "sim": round(sim, 4),
                            "transcript": text,
                        })
                        clip_counter += 1
                        song_clips += 1
                        print(f"    → {clip_id}.wav  (total: {clip_counter})")

                    except Exception as exc:
                        print(f"\n    ERROR seg {seg_idx}: {exc}")
                        continue

                print(f"  Song done: {song_clips} clips")

            except Exception as exc:
                print(f"  ERROR song '{query}': {exc}")
                continue

    finally:
        shutil.rmtree(work_root, ignore_errors=True)

    # Write manifest
    manifest_path = out_dir / "manifest.csv"
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "source", "seg_start_s", "seg_dur_s", "sim", "transcript"])
        writer.writeheader()
        writer.writerows(manifest_rows)

    print(f"\n{'='*60}")
    print(f"Collected : {clip_counter} clips")
    print(f"Output    : {out_dir}")
    print(f"Manifest  : {manifest_path}")
    print(f"\nNext steps:")
    print(f"  1. (Optional) review manifest.csv and delete any bad clips")
    print(f"  2. Import into training set:")
    print(f"     xcopy data\\diffsinger_staging\\japanese\\*.wav data\\singing_training\\clips\\ /Y")
    print(f"     xcopy data\\diffsinger_staging\\japanese\\*.lab data\\singing_training\\clips\\ /Y")
    print(f"  3. Re-run alignment + rebuild:")
    print(f"     .venv\\Scripts\\python.exe experiments\\diffsinger\\prepare_mfa.py")
    print(f"     mfa align data\\mfa_input\\japanese ja_ipa_mfa  data\\mfa_textgrids\\japanese  --beam 50")
    print(f"     .venv\\Scripts\\python.exe experiments\\diffsinger\\build_dataset.py")
    print(f"  4. Binarize + retrain from experiments\\diffsinger\\DiffSinger\\")


if __name__ == "__main__":
    main()
