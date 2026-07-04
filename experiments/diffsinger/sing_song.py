"""
Full DiffSinger singing pipeline — takes any song, outputs Koroki singing it.

Pipeline:
  1. yt-dlp        — download song (original mix, used as timing reference)
  2. yt-dlp search — search for official instrumental on YouTube
  3. demucs        — separate vocals + instrumental from original download
  4. syncedlyrics  — fetch synced lyrics (timestamps + text) from Musixmatch/NetEase/Genius
                     falls back to Whisper if no lyrics found online
  5. SOFA          — force-align phonemes (IPA) on separated vocal
  6. parselmouth   — extract F0 melody from 44.1kHz vocal
  7. DiffSinger    — synthesize Koroki's voice
  8. soundfile     — mix with official instrumental (or demucs fallback)

Usage (from Koroki root, using .venv_diffsinger):
    .venv_diffsinger/Scripts/python.exe experiments/diffsinger/sing_song.py "ado - usseewa"
    .venv_diffsinger/Scripts/python.exe experiments/diffsinger/sing_song.py "yoasobi idol" --start 30 --duration 60
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf

# Force HuggingFace OFFLINE using the DEFAULT cache (~/.cache/huggingface), where the
# whisperx wav2vec2 phoneme aligner + whisper models are cached. This box hits SSL cert
# failures on live HF fetches, which knock the aligner down to coarse AMT and make the
# build reject every segment. Offline-from-default-cache avoids the fetch entirely.
# IMPORTANT: do NOT set HF_HUB_CACHE here — that points lookup away from the default cache
# (the IndexTTS-only checkpoints/hf_cache does not contain the aligner).
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SELF_DIR = Path(__file__).parent
_DIFFSINGER_DIR = _SELF_DIR / "DiffSinger"

_YTDLP = str(Path(sys.executable).parent / "yt-dlp.exe")
if not Path(_YTDLP).exists():
    _YTDLP = "yt-dlp"

_F0_TIMESTEP = 0.005   # 5ms
_MFA_SR = 16000
_TARGET_SR = 44100
_SILENCE_THRESH = 0.28  # seconds — silence gaps used to split into DS segments
#   0.28s catches the ~300-400ms SPs from RMS vowel splitting without fragmenting
#   on the shorter 100-200ms inter-consonant silences SOFA naturally produces
_MAX_SEG_DUR = 7.0     # hard cap: force-split segments longer than this (DiffSinger attention degrades noticeably at ~7.5s+)
_MIN_SEG_DUR = 0.2     # skip segments shorter than this


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def _resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    if orig_sr == target_sr:
        return audio
    from scipy.signal import resample_poly
    g = gcd(orig_sr, target_sr)
    up, down = target_sr // g, orig_sr // g
    if audio.ndim == 1:
        return resample_poly(audio, up, down).astype(np.float32)
    return np.stack(
        [resample_poly(audio[:, c], up, down) for c in range(audio.shape[1])], axis=1
    ).astype(np.float32)


def _extract_f0(audio: np.ndarray, sr: int, duration: float) -> tuple[list[float], float]:
    """Returns (f0_list, voiced_frac) where voiced_frac is the raw fraction of voiced frames
    before any interpolation. Caller should skip synthesis when voiced_frac < 0.05."""
    import parselmouth
    sound = parselmouth.Sound(audio, sampling_frequency=sr)
    # to_pitch_cc (cross-correlation) has fewer octave errors than to_pitch_ac
    # (autocorrelation) for high-pitched Demucs-separated vocals.
    pitch = sound.to_pitch_cc(
        time_step=_F0_TIMESTEP,
        pitch_floor=65.0,
        pitch_ceiling=1100.0,
        voicing_threshold=0.45,
    )
    n_frames = max(1, int(round(duration / _F0_TIMESTEP)))
    vals = []
    for i in range(n_frames):
        v = pitch.get_value_at_time(i * _F0_TIMESTEP)
        vals.append(0.0 if (v is None or np.isnan(v)) else float(v))
    arr = np.array(vals)
    voiced = arr > 0
    voiced_frac = float(voiced.sum()) / max(1, len(voiced))

    if voiced.sum() < 2:
        arr[:] = 200.0
        return [round(float(v), 1) for v in arr], voiced_frac

    # Octave-error correction using a sliding local median (window = 1 second).
    # The old global-median approach cut off legitimate high notes in wide-range
    # songs (e.g. World is Mine): if segment median was 500 Hz, the upper bound
    # was 500*2.2=1100 Hz — borderline — but lower-median segments removed 900 Hz
    # notes as outliers, causing forward-fill to substitute a lower pitch → voice break.
    # A local median adapts to the current phrase, so high notes in a high phrase
    # are judged against a high local median and are never incorrectly cut.
    win = max(1, int(round(1.0 / _F0_TIMESTEP)))  # 1-second window
    local_med = np.zeros_like(arr)
    voiced_vals = arr.copy()
    voiced_vals[~voiced] = np.nan
    for i in range(len(arr)):
        lo, hi = max(0, i - win // 2), min(len(arr), i + win // 2 + 1)
        chunk = voiced_vals[lo:hi]
        valid = chunk[~np.isnan(chunk)]
        local_med[i] = float(np.median(valid)) if len(valid) > 0 else 0.0

    # Octave-halving correction: parselmouth sometimes returns f0/2 when the
    # fundamental is ambiguous (e.g. fast repetitive syllables like ないないない).
    # Use the global voiced median as reference — local_med gets contaminated when
    # many consecutive frames are halved (local windows become all-halved-value,
    # dragging the reference down so the condition never fires).
    # Global median is robust as long as < 50% of frames are halved, which is
    # always the case for localized repetition sections within a longer segment.
    # Must run BEFORE the general outlier pass so halved frames aren't zeroed first.
    global_voiced_med = float(np.median(arr[voiced])) if voiced.sum() > 0 else 0.0
    halved = voiced & (global_voiced_med > 0) & (arr < global_voiced_med * 0.65) & (
        np.abs(arr * 2 - global_voiced_med) < global_voiced_med * 0.30
    )
    if halved.any():
        arr[halved] *= 2

    outliers = voiced & (local_med > 0) & (
        (arr > local_med * 2.5) | (arr < local_med * 0.4)
    )
    if outliers.any():
        arr[outliers] = 0.0
        voiced = arr > 0

    if voiced.sum() < 2:
        arr[:] = float(np.median(arr[arr > 0])) if (arr > 0).any() else 200.0
    else:
        last = float(arr[voiced][0])
        for i in range(len(arr)):
            if voiced[i]:
                last = float(arr[i])
            else:
                arr[i] = last

    # Smooth out single-frame F0 spikes (octave-detection misses) that the vocoder
    # renders as a creak or husky voice break.  5-frame median at 5ms/frame = 25ms
    # window — wide enough to kill spikes, narrow enough to preserve vibrato.
    from scipy.ndimage import median_filter as _median_filter
    if len(arr) >= 5:
        arr = _median_filter(arr, size=5).astype(np.float32)

    return [round(float(v), 1) for v in arr], voiced_frac


def _ramp_f0_before_silence(
    f0: list[float],
    ph_seq: list[str],
    ph_dur: list[float],
    ramp_dur: float = 0.05,
) -> list[float]:
    """Ramp F0 down before AP/SP phonemes to prevent abrupt pitch-to-silence
    transitions, which the vocoder renders as a voice break/creak."""
    arr = np.array(f0, dtype=np.float32)
    ramp_frames = max(1, int(round(ramp_dur / _F0_TIMESTEP)))

    # Build cumulative frame start for each phoneme
    frame_cursor = 0
    for i, (ph, dur) in enumerate(zip(ph_seq, ph_dur)):
        ph_frames = max(1, int(round(dur / _F0_TIMESTEP)))
        next_ph = ph_seq[i + 1] if i + 1 < len(ph_seq) else "SP"
        if ph not in ("AP", "SP") and next_ph in ("AP", "SP"):
            end = frame_cursor + ph_frames
            start = max(frame_cursor, end - ramp_frames)
            if start < end <= len(arr):
                base = arr[start]
                for j in range(start, end):
                    progress = (j - start + 1) / (end - start)
                    arr[j] = base * (1.0 - 0.35 * progress)
        frame_cursor += ph_frames

    return [round(float(v), 1) for v in arr]


_F0_COMFORTABLE_MAX = 700.0  # Hz — approx Koroki/YOASOBI training pitch ceiling (~F5)


def _effective_audio_end(audio: np.ndarray, sr: int, rms_threshold: float = 0.005,
                         window_s: float = 0.5) -> float:
    """Return the timestamp of the last non-silent frame in the audio.
    Scans backward in windows; stops at the first window with RMS above threshold."""
    window = int(window_s * sr)
    n = len(audio)
    t = n
    while t > window:
        chunk = audio[t - window:t]
        if float(np.sqrt(np.mean(chunk ** 2))) >= rms_threshold:
            return t / sr
        t -= window
    return 0.0


def _compute_auto_key_shift(segments: list[dict]) -> tuple[int, float]:
    """Return (semitones_to_shift, p90_hz). Semitones is negative (shift down) or 0.

    Uses the global p90 across all voiced frames — suitable for songs that are
    globally too high for the model.  Songs where only isolated segments are
    extreme are handled by _F0_MODEL_HARD_CEIL clamping instead.
    """
    all_f0 = [float(v) for seg in segments for v in seg["f0_seq"].split() if float(v) > 0]
    if not all_f0:
        return 0, 0.0
    p90 = float(np.percentile(all_f0, 90))
    if p90 <= _F0_COMFORTABLE_MAX:
        return 0, p90
    semitones_over = 12 * np.log2(p90 / _F0_COMFORTABLE_MAX)
    if semitones_over < 1.5:
        return 0, p90
    return max(-int(round(semitones_over)), -12), p90


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def _download(query: str, work_dir: str) -> str:
    out_template = os.path.join(work_dir, "source.%(ext)s")
    cmd = [_YTDLP, "--no-playlist", "--extract-audio", "--audio-format", "wav",
           "--audio-quality", "0", "--output", out_template,
           "--no-warnings", "--no-check-certificates", f"ytsearch1:{query}"]
    print(f"  Downloading: {query}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr[:500]}")
    wav = os.path.join(work_dir, "source.wav")
    if os.path.exists(wav):
        return wav
    for p in Path(work_dir).glob("source.*"):
        return str(p)
    raise FileNotFoundError("yt-dlp produced no output")


_COVER_REJECT = frozenset([
    "covered by", "cover by", "カバー", "アレンジ", "fan made", "fan-made",
    "tribute", "remix", "fan cover",
])


def _try_find_stem(query: str, terms: list[str], stem_name: str, work_dir: str) -> str | None:
    """Search YouTube for an official stem (acapella or instrumental). Returns wav path or None."""
    for term in terms:
        search = f"ytsearch1:{query} {term}"
        # Check title first without downloading
        r = subprocess.run(
            [_YTDLP, "--no-playlist", "--skip-download", "--print", "%(title)s",
             "--no-warnings", search],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0 or not r.stdout.strip():
            continue
        title = r.stdout.strip().lower()
        if not any(t.lower() in title for t in terms):
            continue  # result doesn't match our intent
        if any(kw in title for kw in _COVER_REJECT):
            print(f"  Skipping cover/remix: {r.stdout.strip()[:60]}")
            continue
        # Download the match
        out_template = os.path.join(work_dir, f"{stem_name}.%(ext)s")
        r2 = subprocess.run(
            [_YTDLP, "--no-playlist", "--extract-audio", "--audio-format", "wav",
             "--audio-quality", "0", "--output", out_template, "--no-warnings", search],
            capture_output=True, text=True, timeout=180,
        )
        wav = os.path.join(work_dir, f"{stem_name}.wav")
        if r2.returncode == 0 and os.path.exists(wav):
            print(f"  Found {stem_name} [{term}]: {r.stdout.strip()[:70]}")
            return wav
    return None


def _search_official_stems(query: str, work_dir: str) -> tuple[str | None, str | None]:
    """
    Try to find official acapella + instrumental versions before running demucs.
    Returns official instrumental path, or None.
    Acapella search is intentionally skipped — different YouTube videos for acapella
    and instrumental are never guaranteed to start at the same timestamp, which causes
    DiffSinger vocal offsets to drift out of sync with the instrumental backing track.
    Vocals are always extracted from the original download via demucs (same timeline).
    """
    print("  Searching for official instrumental...")
    INSTR_TERMS = ["instrumental", "off vocal", "off-vocal", "カラオケ", "インスト",
                   "instrumental ver", "instrumental version"]
    instr = _try_find_stem(query, INSTR_TERMS, "official_instr", work_dir)
    if not instr:
        print("  No official instrumental found — will use demucs")
    return instr


def _separate_bs_roformer(source_path: str, output_dir: str) -> tuple[str, str]:
    """Separate vocals using BS-Roformer — handles heavy beats much better than demucs."""
    from audio_separator.separator import Separator

    model_cache = str(_DIFFSINGER_DIR / "checkpoints" / "audio_separator_models")
    Path(model_cache).mkdir(parents=True, exist_ok=True)

    sep = Separator(
        output_dir=output_dir,
        model_file_dir=model_cache,
        output_format="WAV",
        normalization_threshold=0.9,
    )
    sep.load_model("model_bs_roformer_ep_317_sdr_12.9755.ckpt")
    output_files = sep.separate(source_path)

    # sep.separate() returns bare filenames; resolve to full paths under output_dir
    output_files = [
        str(Path(output_dir) / Path(f).name) if not Path(f).is_absolute() else f
        for f in output_files
    ]

    # primary stem = Vocals, secondary = Instrumental
    vocals = next((f for f in output_files if "(Vocals)" in Path(f).name), None)
    instrumental = next((f for f in output_files if "(Instrumental)" in Path(f).name), None)
    if not vocals and len(output_files) >= 1:
        vocals = output_files[0]
    if not instrumental and len(output_files) >= 2:
        instrumental = output_files[1]
    if not vocals or not instrumental:
        raise RuntimeError(f"BS-Roformer output missing expected stems: {output_files}")
    return vocals, instrumental


def _separate_htdemucs(source_path: str, work_dir: str) -> tuple[str, str]:
    """Fallback vocal separation using htdemucs_ft."""
    stems_dir = Path(work_dir) / "stems"
    stems_dir.mkdir(exist_ok=True)
    vocals_path = str(stems_dir / "vocals.wav")
    no_vocals_path = str(stems_dir / "no_vocals.wav")

    try:
        import demucs.api
        sep = demucs.api.Separator(model="htdemucs_ft", two_stems="vocals", progress=False)
        _, separated = sep.separate_audio_file(Path(source_path))
        sf.write(vocals_path, separated["vocals"].cpu().numpy().T, sep.samplerate, subtype="PCM_16")
        sf.write(no_vocals_path, separated["no_vocals"].cpu().numpy().T, sep.samplerate, subtype="PCM_16")
        return vocals_path, no_vocals_path
    except Exception as e:
        print(f"  demucs API failed ({e}), trying runner...")

    runner = _REPO_ROOT / "experiments" / "singing-v2" / "demucs_runner.py"
    out_dir = os.path.join(work_dir, "demucs_out")
    cmd = [sys.executable, str(runner), "--name", "htdemucs_ft",
           "--two-stems", "vocals", "--out", out_dir, source_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"demucs failed:\n{result.stderr[-500:]}")
    stem = Path(source_path).stem
    base = Path(out_dir) / "htdemucs_ft" / stem
    return str(base / "vocals.wav"), str(base / "no_vocals.wav")


def _separate(source_path: str, work_dir: str) -> tuple[str, str]:
    """Separate vocals from instrumental. Tries BS-Roformer first, falls back to htdemucs."""
    bs_out = os.path.join(work_dir, "bs_roformer_out")
    os.makedirs(bs_out, exist_ok=True)
    try:
        print("  Separating vocals (BS-Roformer)...")
        return _separate_bs_roformer(source_path, bs_out)
    except ImportError:
        print("  audio-separator not installed — falling back to htdemucs")
        print("  For better separation: .venv_diffsinger\\Scripts\\pip install \"audio-separator[gpu]\"")
    except Exception as e:
        print(f"  BS-Roformer failed ({e}) — falling back to htdemucs...")

    print("  Separating vocals (htdemucs_ft)...")
    return _separate_htdemucs(source_path, work_dir)


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
    """
    Convert Arabic digits to Japanese kana so MFA can align them.
    Whisper writes numbers as digits; MFA's sudachipy tokenizer treats them as OOV
    and maps them to silence, causing cutoffs wherever numbers appear in lyrics.
    """
    # Nつ counter (みっつ etc.) before generic digit replacement
    text = re.sub(
        r'(\d)つ',
        lambda m: _COUNTER_TSU.get(m.group(1), _DIGITS_JA.get(m.group(1), m.group(1)) + 'つ'),
        text,
    )
    # Multi-digit numbers (longest first to avoid partial replacement)
    for digits, kana in _MULTI_JA:
        text = text.replace(digits, kana)
    # Remaining single digits
    for d, k in _DIGITS_JA.items():
        text = text.replace(d, k)
    return text


def _fetch_synced_lyrics(query: str) -> tuple[str, list[dict]] | None:
    """Fetch synced lyrics (LRC) from online databases via syncedlyrics.
    Returns (full_text, segments) in Whisper segment format, or None if not found.
    Synced lyrics give correct per-line timestamps, replacing Whisper's hallucinated transcript.
    Install: .venv_diffsinger/Scripts/pip install syncedlyrics
    """
    try:
        import syncedlyrics
    except ImportError:
        print("  syncedlyrics not installed — skipping lyrics search")
        print("  Install: .venv_diffsinger\\Scripts\\pip install syncedlyrics")
        return None

    print("  Searching for synced lyrics online...")
    lrc = None
    try:
        lrc = syncedlyrics.search(query)
    except Exception as e:
        print(f"  Lyrics search failed ({e})")

    if not lrc:
        print("  No synced lyrics found — will fall back to Whisper")
        return None

    # Parse LRC: [mm:ss.xx] text  (word-level enhanced LRC uses same format per word)
    raw: list[tuple[float, str]] = []
    for line in lrc.splitlines():
        m = re.match(r'\[(\d+):(\d+(?:\.\d+)?)\](.*)', line.strip())
        if not m:
            continue
        mins, secs, text = m.groups()
        t = int(mins) * 60 + float(secs)
        text = text.strip()
        # Enhanced LRC embeds word timestamps inside the line — strip them
        text = re.sub(r'<\d+:\d+(?:\.\d+)?>', '', text).strip()
        if text:
            raw.append((t, text))

    if not raw:
        print("  Lyrics found but no timed lines could be parsed")
        return None

    segments: list[dict] = []
    for i, (t, text) in enumerate(raw):
        end = raw[i + 1][0] if i + 1 < len(raw) else t + 5.0
        normalized = _normalize_transcript(text)
        segments.append({"start": t, "end": end, "text": normalized})

    full_text = " ".join(s["text"] for s in segments)
    print(f"  Found {len(segments)} synced lyric lines")
    return full_text, segments


def _transcribe(vocals_path: str, whisper_model,
                initial_prompt: str | None = None) -> tuple[str, list[dict]]:
    """Returns (full_normalized_text, segments).
    Each segment is {"start": float, "end": float, "text": str} (normalized).
    Segments drive SP insertion in the SOFA lab file so phrase boundaries are preserved.
    initial_prompt: correct lyrics text (e.g. from Genius) — dramatically reduces
    Whisper hallucinations on heavy production music by anchoring the transcription.
    """
    if initial_prompt:
        print("  Transcribing with Whisper (guided by Genius lyrics)...")
    else:
        print("  Transcribing with Whisper...")
    result = whisper_model.transcribe(
        vocals_path, language="ja",
        condition_on_previous_text=True if initial_prompt else False,
        initial_prompt=initial_prompt,
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.0,
    )
    text = result["text"].strip()
    print(f"  Transcript: {text[:80]}{'...' if len(text) > 80 else ''}")
    normalized = _normalize_transcript(text)
    if normalized != text:
        print(f"  Normalized: {normalized[:80]}{'...' if len(normalized) > 80 else ''}")
    segments = [
        {"start": s["start"], "end": s["end"], "text": _normalize_transcript(s["text"])}
        for s in result.get("segments", [])
    ]
    return normalized, segments


# ---------------------------------------------------------------------------
# SOFA aligner (primary — singing-aware, handles fast/chorus sections)
# ---------------------------------------------------------------------------

# Romaji phoneme set from Greenleaf's JPN_Test2_Plus SOFA model → IPA equivalents
# that our DiffSinger (trained with MFA japanese_mfa) understands.
_SOFA_TO_IPA: dict[str, str] = {
    # vowels
    "a": "a",   "i": "i",   "u": "ɯ",   "e": "e",   "o": "o",
    # plain consonants
    "k": "k",   "g": "ɡ",   "s": "s",   "z": "z",
    "t": "t",   "d": "d",   "n": "n",   "h": "h",
    "m": "m",   "p": "p",   "b": "b",   "r": "ɾ",
    "w": "w",   "y": "j",   "f": "ɸ",   "v": "b",
    # digraphs / clusters
    "sh": "ɕ",  "ch": "tɕ", "ts": "ts",
    "ky": "c",  "gy": "ɡ",  "ny": "ɲ",
    "hy": "ç",  "my": "mʲ", "py": "pʲ",
    "by": "bʲ", "ry": "ɾʲ", "ty": "tʲ",
    "dy": "dʲ", "kw": "k",  "gw": "ɡ",
    # special
    "N": "ɴ",   "cl": "ʔ",  "ng": "ŋ",
    # silence / breath / boundary markers
    "SP": "SP", "AP": "AP", "pau": "SP",
    "EP": "SP",  # end-pause marker
    "GS": "SP",  # glottal/devoicing marker — treat as brief silence
}

_SOFA_RUNNER = Path(__file__).parent / "sofa_runner.py"
_SOFA_CKPT = Path(__file__).parent / "SOFA" / "checkpoints" / "japanese" / "step.100000.ckpt"


def _sofa_available() -> bool:
    return _SOFA_RUNNER.exists() and _SOFA_CKPT.exists()


def _run_sofa(vocals_path: str, transcript: str, work_dir: str,
              segments: list[dict] | None = None) -> str:
    """Run SOFA aligner on the full vocal track. Returns path to output TextGrid.
    segments: Whisper segment dicts used to insert SP tokens at phrase boundaries.
    """
    print("  Running SOFA alignment (singing-aware)...")
    sofa_out = Path(work_dir) / "sofa_out"
    sofa_out.mkdir(exist_ok=True)

    cmd = [sys.executable, str(_SOFA_RUNNER),
           vocals_path,
           "--transcript", transcript,
           "--output-dir", str(sofa_out)]
    if segments:
        cmd += ["--segments-json", json.dumps(segments)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.stderr:
        for line in result.stderr.splitlines():
            if line.strip():
                print(f"  [sofa] {line.strip()}")
    if result.returncode != 0:
        raise RuntimeError(f"SOFA runner failed:\n{result.stderr[-600:]}")

    tg_path = result.stdout.strip().splitlines()[-1].strip()
    if not Path(tg_path).exists():
        raise RuntimeError(f"SOFA reported TextGrid path '{tg_path}' but file not found")
    return tg_path


def _run_sofa_segmented(
    vocals_path: str,
    lyrics_segs: list[dict],
    work_dir: str,
) -> list[tuple[float, float, str]]:
    """
    Run SOFA independently on each lyric line's audio clip.

    Instead of aligning the whole song at once (where SOFA can compress entire
    phrases into short bursts), this extracts [start-pad, end+pad] audio for
    each synced-lyrics line and aligns only that clip. The synced timestamp
    becomes a hard audio boundary — SOFA is physically unable to put phonemes
    outside the clip window.

    Side effect: DS segments produced from these intervals are sentence-length,
    avoiding DiffSinger attention degradation on long (10-15s) inputs.

    Returns IPA-mapped intervals with absolute timestamps, sorted by start time.
    """
    _sofa_dir = _SELF_DIR / "SOFA"
    _ckpt = _sofa_dir / "checkpoints" / "japanese" / "step.100000.ckpt"
    _dict = _sofa_dir / "checkpoints" / "japanese" / "japanese-extension-sofa.txt"

    sys.path.insert(0, str(_SELF_DIR))
    from sofa_runner import _kana_to_sofa_lab  # type: ignore

    audio, sr = sf.read(vocals_path)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float32)
    total_dur = len(audio) / sr

    clips_dir = Path(work_dir) / "sofa_clips"
    clips_dir.mkdir(exist_ok=True)
    (clips_dir / "TextGrid").mkdir(exist_ok=True)

    _PAD = 0.25  # seconds of audio context on each side of the lyric window
    clip_meta = []

    for i, seg in enumerate(lyrics_segs):
        text = seg["text"].strip()
        if not text:
            continue
        lab = _kana_to_sofa_lab(text)
        if not lab.strip():
            continue

        t_start = float(seg["start"])
        t_end   = float(seg["end"])
        t0 = max(0.0, t_start - _PAD)
        t1 = min(total_dur, t_end + _PAD)

        s, e = int(t0 * sr), int(t1 * sr)
        clip = audio[s:e]

        name = f"line_{i:04d}"
        sf.write(str(clips_dir / f"{name}.wav"), clip, sr, subtype="PCM_16")
        (clips_dir / f"{name}.lab").write_text(lab, encoding="utf-8")
        clip_meta.append({
            "name": name,
            "t0": t0,
            "t_start": t_start,
            "t_end": t_end,
        })

    if not clip_meta:
        raise RuntimeError("No valid lyric segments to align")

    print(f"  Running SOFA on {len(clip_meta)} lyric clips (per-line mode)...")
    cmd = [
        sys.executable, str(_sofa_dir / "infer.py"),
        "--ckpt", str(_ckpt),
        "--folder", str(clips_dir),
        "--dictionary", str(_dict),
        "--out_formats", "TextGrid",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            cwd=str(_sofa_dir), timeout=600)
    if result.stderr:
        for line in result.stderr.splitlines():
            if line.strip() and "warning" not in line.lower():
                print(f"  [sofa] {line.strip()}")
    if result.returncode != 0:
        raise RuntimeError(f"SOFA per-line failed:\n{result.stderr[-500:]}")

    all_intervals: list[tuple[float, float, str]] = []
    missing = 0
    for meta in clip_meta:
        tg_path = clips_dir / "TextGrid" / f"{meta['name']}.TextGrid"
        if not tg_path.exists():
            candidates = sorted((clips_dir / "TextGrid").glob(f"{meta['name']}*.TextGrid"))
            if not candidates:
                missing += 1
                continue
            tg_path = candidates[0]

        raw = _parse_textgrid(str(tg_path))
        for xmin, xmax, label in raw:
            abs_min = meta["t0"] + xmin
            abs_max = meta["t0"] + xmax
            # Clamp to true lyric window (strip padding frames)
            abs_min = max(abs_min, meta["t_start"])
            abs_max = min(abs_max, meta["t_end"])
            if abs_max > abs_min + 0.001:
                ipa = _SOFA_TO_IPA.get(label, label)
                all_intervals.append((abs_min, abs_max, ipa))

    if missing:
        print(f"  WARN: {missing} lyric clips produced no TextGrid")
    all_intervals.sort(key=lambda x: x[0])
    print(f"  Per-line alignment: {len(all_intervals)} intervals from {len(clip_meta)} lines")
    return all_intervals


def _parse_textgrid_sofa(path: str) -> list[tuple[float, float, str]]:
    """Parse SOFA TextGrid and remap Romaji labels → IPA for DiffSinger."""
    raw = _parse_textgrid(path)
    result = []
    for xmin, xmax, label in raw:
        ipa = _SOFA_TO_IPA.get(label, label)  # unmapped → pass through → normalize_phone handles it
        result.append((xmin, xmax, ipa))
    return result


# ---------------------------------------------------------------------------
# Basic Pitch AMT alignment (primary path — replaces SOFA when available)
# ---------------------------------------------------------------------------
# Maps hiragana mora → IPA phoneme list in DiffSinger's japanese_mfa vocab.
# One mora = one note in J-pop ~90% of the time; this lets us derive phoneme
# timing directly from AMT note onsets instead of forced acoustic alignment.
_MORA_TO_IPA: dict[str, list[str]] = {
    # pure vowels
    "あ": ["a"], "い": ["i"], "う": ["ɯ"], "え": ["e"], "お": ["o"],
    # k-row
    "か": ["k", "a"], "き": ["c", "i"], "く": ["k", "ɯ"], "け": ["k", "e"], "こ": ["k", "o"],
    "きゃ": ["c", "a"], "きゅ": ["c", "ɯ"], "きょ": ["c", "o"],
    # s-row
    "さ": ["s", "a"], "し": ["ɕ", "i"], "す": ["s", "ɯ"], "せ": ["s", "e"], "そ": ["s", "o"],
    "しゃ": ["ɕ", "a"], "しゅ": ["ɕ", "ɯ"], "しょ": ["ɕ", "o"],
    # t-row
    "た": ["t", "a"], "ち": ["tɕ", "i"], "つ": ["ts", "ɯ"], "て": ["t", "e"], "と": ["t", "o"],
    "ちゃ": ["tɕ", "a"], "ちゅ": ["tɕ", "ɯ"], "ちょ": ["tɕ", "o"],
    # n-row
    "な": ["n", "a"], "に": ["ɲ", "i"], "ぬ": ["n", "ɯ"], "ね": ["n", "e"], "の": ["n", "o"],
    "にゃ": ["ɲ", "a"], "にゅ": ["ɲ", "ɯ"], "にょ": ["ɲ", "o"],
    # h-row
    "は": ["h", "a"], "ひ": ["ç", "i"], "ふ": ["ɸ", "ɯ"], "へ": ["h", "e"], "ほ": ["h", "o"],
    "ひゃ": ["ç", "a"], "ひゅ": ["ç", "ɯ"], "ひょ": ["ç", "o"],
    # m-row
    "ま": ["m", "a"], "み": ["mʲ", "i"], "む": ["m", "ɯ"], "め": ["m", "e"], "も": ["m", "o"],
    "みゃ": ["mʲ", "a"], "みゅ": ["mʲ", "ɯ"], "みょ": ["mʲ", "o"],
    # y-row
    "や": ["j", "a"], "ゆ": ["j", "ɯ"], "よ": ["j", "o"],
    # r-row
    "ら": ["ɾ", "a"], "り": ["ɾʲ", "i"], "る": ["ɾ", "ɯ"], "れ": ["ɾ", "e"], "ろ": ["ɾ", "o"],
    "りゃ": ["ɾʲ", "a"], "りゅ": ["ɾʲ", "ɯ"], "りょ": ["ɾʲ", "o"],
    # w-row
    "わ": ["w", "a"], "ゐ": ["w", "i"], "ゑ": ["w", "e"], "を": ["w", "o"],
    # special
    "ん": ["ɴ"],
    "っ": ["ʔ"],
    "ー": [],  # long vowel mark — no new phoneme, extends previous
    # g-row
    "が": ["ɡ", "a"], "ぎ": ["ɡ", "i"], "ぐ": ["ɡ", "ɯ"], "げ": ["ɡ", "e"], "ご": ["ɡ", "o"],
    "ぎゃ": ["ɡ", "a"], "ぎゅ": ["ɡ", "ɯ"], "ぎょ": ["ɡ", "o"],
    # z-row
    "ざ": ["z", "a"], "じ": ["dʑ", "i"], "ず": ["z", "ɯ"], "ぜ": ["z", "e"], "ぞ": ["z", "o"],
    "じゃ": ["dʑ", "a"], "じゅ": ["dʑ", "ɯ"], "じょ": ["dʑ", "o"],
    # d-row
    "だ": ["d", "a"], "ぢ": ["dʑ", "i"], "づ": ["z", "ɯ"], "で": ["d", "e"], "ど": ["d", "o"],
    # b-row
    "ば": ["b", "a"], "び": ["bʲ", "i"], "ぶ": ["b", "ɯ"], "べ": ["b", "e"], "ぼ": ["b", "o"],
    "びゃ": ["bʲ", "a"], "びゅ": ["bʲ", "ɯ"], "びょ": ["bʲ", "o"],
    # p-row
    "ぱ": ["p", "a"], "ぴ": ["pʲ", "i"], "ぷ": ["p", "ɯ"], "ぺ": ["p", "e"], "ぽ": ["p", "o"],
    "ぴゃ": ["pʲ", "a"], "ぴゅ": ["pʲ", "ɯ"], "ぴょ": ["pʲ", "o"],
    # extended combinations (foreign loanwords)
    "てぃ": ["t", "i"], "でぃ": ["d", "i"], "とぅ": ["t", "ɯ"], "どぅ": ["d", "ɯ"],
    "ふぁ": ["ɸ", "a"], "ふぃ": ["ɸ", "i"], "ふぇ": ["ɸ", "e"], "ふぉ": ["ɸ", "o"],
    "ヴぁ": ["b", "a"], "ヴぃ": ["b", "i"], "ヴぇ": ["b", "e"], "ヴぉ": ["b", "o"],
}
# Small hiragana that combine with the preceding char to form a compound mora
_SMALL_HIRA = frozenset("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮ")


def _split_morae(text: str) -> list[str]:
    """Split a hiragana/katakana string into mora units.

    - small kana (ゃゅょ etc.) attach to preceding char → compound mora
    - っ/ッ = single mora (geminate stop, maps to ʔ)
    - ん/ン = single mora (nasal coda, maps to ɴ)
    - ー = long vowel extension (appended as-is; caller handles it)
    - All other kana = single mora (1 char)
    """
    # Katakana → hiragana normalisation
    text = "".join(
        chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c
        for c in text
    )
    morae: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if i + 1 < len(text) and text[i + 1] in _SMALL_HIRA:
            morae.append(c + text[i + 1])
            i += 2
        elif "ぁ" <= c <= "ゟ":
            morae.append(c)
            i += 1
        else:
            i += 1  # skip non-kana (kanji already converted by pykakasi, punctuation, etc.)
    return morae


def _basic_pitch_available() -> bool:
    try:
        import basic_pitch  # noqa: F401
        return True
    except ImportError:
        return False


def _crepe_available() -> bool:
    try:
        import torchcrepe  # noqa: F401
        return True
    except ImportError:
        return False


def _whisperx_available() -> bool:
    try:
        import whisperx  # noqa: F401
        return True
    except ImportError:
        return False


def _run_wav2vec2_align(
    vocals_path: str,
    lyrics_segs: list[dict],
    work_dir: str,
) -> list[tuple[float, float, str]]:
    """Forced alignment using Wav2Vec2 CTC (via whisperx).

    Audio-grounded — listens to actual vocal instead of counting notes.
    More robust than SOFA on rapid sections. Same output format as SOFA.
    Downloads ~300MB Japanese Wav2Vec2 model on first use (then HF-cached).
    Results cached as wav2vec2_intervals.json — delete to force re-run.
    """
    cache_path = Path(work_dir) / "wav2vec2_intervals.json"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        print(f"  [resume] Wav2Vec2 intervals → wav2vec2_intervals.json ({len(data)} intervals)")
        return [(float(d[0]), float(d[1]), d[2]) for d in data]
    import torch
    import whisperx
    import librosa
    import pykakasi

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print("  Loading Wav2Vec2 alignment model for Japanese...")
    try:
        model_a, metadata = whisperx.load_align_model(language_code="ja", device=device)
    except Exception as exc:
        raise RuntimeError(f"Wav2Vec2 model load failed: {exc}") from exc

    audio_16k, _ = librosa.load(vocals_path, sr=16000, mono=True)
    audio_dur = len(audio_16k) / 16000
    kks = pykakasi.kakasi()

    _WORD_OVERRIDES: dict[str, str] = {
        "君": "きみ", "僕": "ぼく", "俺": "おれ", "貴方": "あなた", "貴女": "あなた",
        "彼女": "かのじょ", "彼": "かれ", "心": "こころ", "夢": "ゆめ", "空": "そら",
        "星": "ほし", "花": "はな", "涙": "なみだ", "声": "こえ", "手": "て",
        "目": "め", "日": "ひ", "今": "いま", "何": "なに", "誰": "だれ",
        "何処": "どこ", "此処": "ここ", "何時": "いつ",
    }

    def _to_hira(text: str) -> str:
        hira = ""
        for item in kks.convert(text):
            orig = item["orig"]
            hira += _WORD_OVERRIDES[orig] if orig in _WORD_OVERRIDES else item.get("hira", orig)
        return hira

    intervals: list[tuple[float, float, str]] = []
    failed = 0
    total = 0
    _prev_actual_end = 0.0  # tracks where the previous line's phonemes actually ended

    for seg in lyrics_segs:
        text = seg["text"].strip()
        if not text:
            continue
        total += 1
        t_start = float(seg["start"])
        t_end = float(seg["end"])

        if t_start >= audio_dur:
            continue
        t_end = min(t_end, audio_dur)

        # If previous line's phonemes ended early and there's voiced audio between that
        # end and this line's LRC start, the LRC timestamp is late — pull t_start back
        # so the missed singing gets covered. (e.g. LRC splits a continuous phrase with
        # a 3s timestamp gap while the singer never actually stopped.)
        if _prev_actual_end > 0 and t_start - _prev_actual_end > 0.2:
            gap_s = int(_prev_actual_end * 16000)
            gap_e = int(t_start * 16000)
            gap_clip = audio_16k[gap_s:gap_e]
            if len(gap_clip) > 0:
                gap_rms = float(np.sqrt(np.mean(gap_clip ** 2)))
                if gap_rms > 0.02:
                    t_start = _prev_actual_end

        hira = _to_hira(text)
        if not hira.strip():
            continue

        _line_start_idx = len(intervals)  # mark where this line's intervals begin

        s = int(t_start * 16000)
        e = int(t_end * 16000)
        clip = audio_16k[s:e]
        if len(clip) < 1600:  # < 100ms
            continue

        clip_dur = len(clip) / 16000
        clip_rms = float(np.sqrt(np.mean(clip ** 2)))
        if clip_rms < 0.01:
            continue  # silence — no vocal signal, skip before Wav2Vec2

        try:
            result = whisperx.align(
                [{"start": 0.0, "end": clip_dur, "text": hira}],
                model_a, metadata, clip, device,
                return_char_alignments=True,
                print_progress=False,
            )
        except Exception:
            failed += 1
            continue

        for r_seg in result.get("segments", []):
            chars = r_seg.get("chars", [])
            if not chars:
                failed += 1
                break

            # How many chars have valid (non-None) start timestamps?
            valid_idx = [i for i, c in enumerate(chars) if c.get("start") is not None]
            valid_ratio = len(valid_idx) / len(chars)

            # Also check coverage: whisperx sometimes returns only a prefix of the text
            # (stops partway through the line). If it returned < 70% of expected characters,
            # the last returned char will be artificially stretched to clip_dur → long choke note.
            coverage_ratio = len(chars) / max(1, len(hira))

            if valid_ratio < 0.5 or coverage_ratio < 0.7:
                # Wav2Vec2 failed on this line — proportional fallback across [t_start, t_end]
                morae = _split_morae(hira)
                if morae:
                    mora_dur = (t_end - t_start) / len(morae)
                    for j, mora in enumerate(morae):
                        m_s = t_start + j * mora_dur
                        m_e = t_start + (j + 1) * mora_dur
                        phones = _MORA_TO_IPA.get(mora)
                        if not phones:
                            continue
                        md = m_e - m_s
                        if len(phones) == 1:
                            intervals.append((m_s, m_e, phones[0]))
                        else:
                            cd = min(0.055, max(0.020, md * 0.28))
                            if md - cd < cd:
                                cd = md / 2.0
                            intervals.append((m_s, m_s + cd, phones[0]))
                            intervals.append((m_s + cd, m_e, phones[1]))
                _prev_actual_end = t_end
                continue

            # Interpolate None start times between valid anchors so no char piles at t=0
            anchors = [(i, chars[i]["start"]) for i in valid_idx]
            interp = [None] * len(chars)
            for i, t in anchors:
                interp[i] = t
            # gaps between anchors
            for k in range(len(anchors) - 1):
                i0, t0 = anchors[k]
                i1, t1 = anchors[k + 1]
                for j in range(i0 + 1, i1):
                    interp[j] = t0 + (t1 - t0) * (j - i0) / (i1 - i0)
            # prefix before first anchor
            if anchors[0][0] > 0:
                t0 = anchors[0][1]
                n_pre = anchors[0][0]
                for j in range(n_pre):
                    interp[j] = t0 * (j + 1) / (n_pre + 1)
            # suffix after last anchor
            last_i, last_t = anchors[-1]
            if last_i < len(chars) - 1:
                remaining = clip_dur - last_t
                n_suf = len(chars) - 1 - last_i
                for j in range(last_i + 1, len(chars)):
                    interp[j] = last_t + remaining * (j - last_i) / (n_suf + 1)
            # Build end times: prefer Wav2Vec2's reported end; last char uses its actual end
            # NOT clip_dur — this prevents the last aligned phoneme from being stretched
            # across the entire uncovered tail of the line (the root cause of the choke).
            wa2v2_ends = [c.get("end") for c in chars]
            interp_end = []
            for _ii in range(len(chars) - 1):
                interp_end.append(
                    wa2v2_ends[_ii] if wa2v2_ends[_ii] is not None else (interp[_ii + 1] or clip_dur)
                )
            last_wa2v2_end = wa2v2_ends[-1] if wa2v2_ends else None
            interp_end.append(last_wa2v2_end if last_wa2v2_end is not None else clip_dur)

            i = 0
            while i < len(chars):
                char = chars[i].get("char", "")
                c_start = (interp[i] or 0.0) + t_start
                if i + 1 < len(chars) and chars[i + 1].get("char", "") in _SMALL_HIRA:
                    mora = char + chars[i + 1].get("char", "")
                    m_end = (interp_end[i + 1] or interp_end[i] or c_start + 0.05) + t_start
                    i += 2
                else:
                    mora = char
                    m_end = (interp_end[i] or c_start + 0.05) + t_start
                    i += 1
                phones = _MORA_TO_IPA.get(mora)
                if phones is None:
                    intervals.append((c_start, m_end, "SP"))
                    continue
                if not phones:
                    # ー long vowel — stretch last interval to cover it instead of leaving a gap
                    if intervals:
                        prev = intervals[-1]
                        intervals[-1] = (prev[0], max(prev[1], m_end), prev[2])
                    continue
                m_dur = max(0.020, m_end - c_start)
                m_end = c_start + m_dur
                if len(phones) == 1:
                    intervals.append((c_start, m_end, phones[0]))
                else:
                    con_dur = min(0.055, max(0.020, m_dur * 0.28))
                    if m_dur - con_dur < con_dur:
                        con_dur = m_dur / 2.0
                    intervals.append((c_start, c_start + con_dur, phones[0]))
                    intervals.append((c_start + con_dur, m_end, phones[1]))

            # Add a leading SP before the first phoneme of each line so DiffSinger gets
            # a natural phrase-entry breath — matching what SOFA always produces.
            if len(intervals) > _line_start_idx:
                _first_iv = intervals[_line_start_idx]
                _sp_end = _first_iv[0]
                _sp_start = max(t_start, _sp_end - 0.025)
                if _sp_end - _sp_start >= 0.010:
                    intervals.insert(_line_start_idx, (_sp_start, _sp_end, "SP"))

            # If all chars placed but last vowel is very short before a large trailing gap,
            # extend it to avoid a burst-then-silence artifact at phrase endings.
            # (Wav2Vec2 squishes final morae to single frames when a musical pause follows.)
            if intervals:
                _VOWELS_SET = frozenset({"a", "i", "ɯ", "e", "o", "ɴ"})
                _last_end_abs = intervals[-1][1]
                _phrase_gap = t_end - _last_end_abs
                if _phrase_gap > 0.3:
                    for _ri in range(len(intervals) - 1, -1, -1):
                        _s, _e, _ph = intervals[_ri]
                        if _s < t_start:
                            break  # don't cross into a previous line
                        if _ph in _VOWELS_SET and (_e - _s) < 0.060:
                            _ext = min(0.120, _phrase_gap * 0.5, 0.060 - (_e - _s) + 0.060)
                            intervals[_ri] = (_s, _e + _ext, _ph)
                            for _rj in range(_ri + 1, len(intervals)):
                                _sa, _ea, _pha = intervals[_rj]
                                intervals[_rj] = (_sa + _ext, _ea + _ext, _pha)
                            break

            # Track where this line's phonemes actually ended for next-line gap detection
            if intervals:
                _prev_actual_end = intervals[-1][1]

            # If Wav2Vec2 only covered a prefix of the line, distribute the uncovered tail
            # proportionally so those morae are heard rather than silent.
            last_covered_abs = (interp_end[-1] or 0.0) + t_start
            tail_dur = t_end - last_covered_abs
            if tail_dur > 0.15 and len(chars) < len(hira):
                tail_hira = hira[len(chars):]
                tail_morae = _split_morae(tail_hira)
                if tail_morae:
                    m_each = tail_dur / len(tail_morae)
                    for _jj, mora in enumerate(tail_morae):
                        m_s = last_covered_abs + _jj * m_each
                        m_e = last_covered_abs + (_jj + 1) * m_each
                        phones = _MORA_TO_IPA.get(mora)
                        if not phones:
                            continue
                        md = m_e - m_s
                        if len(phones) == 1:
                            intervals.append((m_s, m_e, phones[0]))
                        else:
                            cd = min(0.055, max(0.020, md * 0.28))
                            if md - cd < cd:
                                cd = md / 2.0
                            intervals.append((m_s, m_s + cd, phones[0]))
                            intervals.append((m_s + cd, m_e, phones[1]))

    intervals.sort(key=lambda x: x[0])
    print(f"  Wav2Vec2 alignment: {len(intervals)} intervals, {failed}/{total} lines failed")
    if total > 0 and failed / total > 0.4:
        raise RuntimeError(f"Too many Wav2Vec2 failures ({failed}/{total}) — falling back")
    cache_path.write_text(
        json.dumps([[iv[0], iv[1], iv[2]] for iv in intervals], ensure_ascii=False),
        encoding="utf-8",
    )
    return intervals


def _run_crepe(
    vocals_path: str,
    work_dir: str,
    conf_threshold: float = 0.45,
    pitch_jump_semitones: float = 2.0,
    smooth_ms: float = 50.0,
    min_note_dur_ms: float = 40.0,
    min_frequency: float = 65.0,
    max_frequency: float = 1100.0,
) -> list[tuple[float, float, float]]:
    """Run torchcrepe pitch tracker on isolated vocal. Returns (onset_s, offset_s, pitch_hz).

    Monophonic by design — more accurate than Basic Pitch for isolated vocal (no polyphony).
    smooth_ms suppresses vibrato before note segmentation so pitch ornaments don't create splits.
    Results cached as crepe_notes.json — delete to force re-run.
    """
    cache_path = Path(work_dir) / "crepe_notes.json"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        notes = json.loads(cache_path.read_text(encoding="utf-8"))
        print(f"  [resume] CREPE notes → crepe_notes.json ({len(notes)} notes)")
        return [(float(n[0]), float(n[1]), float(n[2])) for n in notes]

    print("  Running CREPE pitch tracker (monophonic vocal)...")
    import torch
    import torchcrepe
    import librosa
    from scipy.signal import medfilt

    audio, _ = librosa.load(vocals_path, sr=16000, mono=True)
    audio_tensor = torch.tensor(audio).unsqueeze(0)  # (1, T)

    hop_length = 160  # 10ms at 16kHz
    frame_dur = hop_length / 16000

    device = "cuda" if torch.cuda.is_available() else "cpu"
    pitch, periodicity = torchcrepe.predict(
        audio_tensor,
        16000,
        hop_length=hop_length,
        fmin=min_frequency,
        fmax=max_frequency,
        model="full",
        batch_size=512,
        device=device,
        return_periodicity=True,
        decoder=torchcrepe.decode.viterbi,
    )

    pitch = pitch.squeeze(0).cpu().numpy()
    periodicity = periodicity.squeeze(0).cpu().numpy()
    n_frames = len(pitch)
    times = np.arange(n_frames) * frame_dur
    voiced = periodicity > conf_threshold

    # Median-smooth pitch over voiced regions to suppress vibrato before segmentation
    smooth_frames = max(1, int(smooth_ms / (frame_dur * 1000)))
    if smooth_frames % 2 == 0:
        smooth_frames += 1
    smoothed = np.where(voiced, pitch, 0.0)
    if smooth_frames > 1:
        smoothed = medfilt(smoothed.astype(float), kernel_size=smooth_frames)

    def _to_semitone(hz: np.ndarray) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(hz > 0, 12 * np.log2(hz / 440.0) + 69, np.nan)

    smoothed_st = _to_semitone(smoothed)

    min_frames = max(1, int(min_note_dur_ms / 1000.0 / frame_dur))
    notes: list[tuple[float, float, float]] = []
    i = 0
    while i < n_frames:
        if not voiced[i]:
            i += 1
            continue
        note_start = i
        j = i + 1
        while j < n_frames and voiced[j]:
            prev_st = smoothed_st[j - 1]
            curr_st = smoothed_st[j]
            if not (np.isnan(prev_st) or np.isnan(curr_st)):
                if abs(float(curr_st) - float(prev_st)) > pitch_jump_semitones:
                    break
            j += 1
        if j - note_start >= min_frames:
            onset_s = float(times[note_start])
            offset_s = float(times[j - 1]) + frame_dur
            note_pitches = pitch[note_start:j]
            note_pitches = note_pitches[note_pitches > 0]
            if len(note_pitches) > 0:
                pitch_hz = float(np.median(note_pitches))
                if min_frequency <= pitch_hz <= max_frequency:
                    notes.append((onset_s, offset_s, pitch_hz))
        i = j

    notes.sort(key=lambda x: x[0])
    print(f"  CREPE: {len(notes)} notes detected (device={device})")
    cache_path.write_text(json.dumps([[n[0], n[1], n[2]] for n in notes]), encoding="utf-8")
    return notes


def _run_basic_pitch(
    vocals_path: str,
    work_dir: str,
    onset_threshold: float = 0.20,   # lowered from 0.35 — captures quiet/sustained outro notes
    frame_threshold: float = 0.25,   # lowered to sustain notes through softer passages
    minimum_note_length: float = 40.0,  # lowered from 80ms — 80ms missed fast notes (~50ms/mora in outro)
    minimum_frequency: float = 65.0,
    maximum_frequency: float = 1100.0,
) -> list[tuple[float, float, float]]:
    """Run Basic Pitch AMT on vocals track. Returns (onset_s, offset_s, pitch_hz) per note.

    Results cached as amt_notes.json — delete to force re-run.
    """
    cache_path = Path(work_dir) / "amt_notes.json"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        notes = json.loads(cache_path.read_text(encoding="utf-8"))
        print(f"  [resume] AMT notes → amt_notes.json ({len(notes)} notes)")
        return [(float(n[0]), float(n[1]), float(n[2])) for n in notes]

    print("  Running Basic Pitch AMT (note detection)...")
    from basic_pitch.inference import predict  # type: ignore

    _, _, note_events = predict(
        vocals_path,
        onset_threshold=onset_threshold,
        frame_threshold=frame_threshold,
        minimum_note_length=minimum_note_length,
        minimum_frequency=minimum_frequency,
        maximum_frequency=maximum_frequency,
        multiple_pitch_bends=False,
    )

    notes: list[tuple[float, float, float]] = []
    for ev in note_events:
        onset_s = float(ev[0])
        offset_s = float(ev[1])
        pitch_midi = int(ev[2])
        pitch_hz = 440.0 * (2.0 ** ((pitch_midi - 69) / 12.0))
        dur_ms = (offset_s - onset_s) * 1000
        if dur_ms >= minimum_note_length:
            notes.append((onset_s, offset_s, pitch_hz))

    notes.sort(key=lambda x: x[0])
    print(f"  Basic Pitch: {len(notes)} notes detected")
    cache_path.write_text(json.dumps([[n[0], n[1], n[2]] for n in notes]), encoding="utf-8")
    return notes


def _merge_notes_to_count(
    notes: list[tuple[float, float, float]], target: int
) -> list[tuple[float, float, float]]:
    """Merge adjacent notes until len == target. Merges pairs with smallest gap first
    (tied/melisma notes have the smallest inter-note gap)."""
    result = list(notes)
    while len(result) > target and len(result) > 1:
        best_i = min(
            range(len(result) - 1),
            key=lambda i: (result[i + 1][0] - result[i][1]) * 10
            + (result[i][1] - result[i][0])
            + (result[i + 1][1] - result[i + 1][0]),
        )
        a, b = result[best_i], result[best_i + 1]
        dur_a = a[1] - a[0]
        dur_b = b[1] - b[0]
        merged_pitch = (a[2] * dur_a + b[2] * dur_b) / (dur_a + dur_b) if (dur_a + dur_b) > 0 else a[2]
        result[best_i : best_i + 2] = [(a[0], b[1], merged_pitch)]
    return result


def _split_notes_to_count(
    notes: list[tuple[float, float, float]], target: int
) -> list[tuple[float, float, float]]:
    """Split longest notes in half until len == target."""
    result = list(notes)
    while len(result) < target:
        longest_i = max(range(len(result)), key=lambda i: result[i][1] - result[i][0])
        n = result[longest_i]
        mid = (n[0] + n[1]) / 2.0
        result[longest_i : longest_i + 1] = [(n[0], mid, n[2]), (mid, n[1], n[2])]
    return result


def _align_amt_notes_to_lyrics(
    midi_notes: list[tuple[float, float, float]],
    lyric_segs: list[dict],
) -> list[tuple[float, float, str]]:
    """Align Basic Pitch notes to lyric segment mora sequences → IPA intervals.

    For each lyric line:
      1. Filter MIDI notes to line's timestamp window (±200ms tolerance)
      2. Split hiragana text into morae via pykakasi + _split_morae
      3. Merge / split notes to match mora count
      4. Assign IPA phonemes to each note's duration (C gets 40ms, V gets the rest)

    Returns intervals in same format as SOFA: list of (start_s, end_s, ipa_phone).
    Raises RuntimeError if > 40% of segments fail note matching (caller falls back to SOFA).
    """
    try:
        import pykakasi
        kks = pykakasi.kakasi()
    except ImportError as e:
        raise RuntimeError(f"pykakasi not available: {e}") from e

    # Word-level overrides applied to pykakasi token stream before joining.
    # Key = orig (kanji) token, value = forced hira reading.
    _WORD_OVERRIDES: dict[str, str] = {
        "君": "きみ",
        "僕": "ぼく",
        "俺": "おれ",
        "貴方": "あなた",
        "貴女": "あなた",
        "彼女": "かのじょ",
        "彼": "かれ",
        "心": "こころ",
        "夢": "ゆめ",
        "空": "そら",
        "星": "ほし",
        "花": "はな",
        "涙": "なみだ",
        "声": "こえ",
        "手": "て",
        "目": "め",
        "日": "ひ",
        "今": "いま",
        "何": "なに",
        "誰": "だれ",
        "何処": "どこ",
        "此処": "ここ",
        "何時": "いつ",
    }

    # Text-level replacements applied BEFORE pykakasi (longest match first).
    # Used for compound words pykakasi misreads as a unit (e.g. 愛してる→いとしてる).
    _TEXT_PRE_FIXES: list[tuple[str, str]] = [
        ("愛されたことも", "あいされたことも"),
        ("愛したこともない", "あいしたこともない"),
        ("愛したこと", "あいしたこと"),
        ("愛してるで", "あいしてるで"),
        ("愛してるよ", "あいしてるよ"),
        ("愛してる", "あいしてる"),
        ("愛してた", "あいしてた"),
        ("愛してない", "あいしてない"),
        ("愛して", "あいして"),
        ("愛する", "あいする"),
        ("愛した", "あいした"),
        ("愛せ", "あいせ"),
        ("愛だ", "あいだ"),
        ("愛の", "あいの"),
        ("愛を", "あいを"),
        ("愛が", "あいが"),
        ("愛は", "あいは"),
        ("愛と", "あいと"),
        # 陰 alone reads as いん (yin) in Sino-Japanese; おかげ compounds must be forced
        ("お陰", "おかげ"),
    ]

    def _kakasi_to_hira(text: str) -> str:
        """Convert text to hiragana, applying pre-fixes + word-level overrides.

        Order of operations:
          1. Text-level substitutions (handles compound misreadings like 愛してる)
          2. 々 expansion → double the preceding kanji before pykakasi reads it
          3. pykakasi token-level overrides (handles single-kanji misreadings like 君)
        """
        # 1. Text-level substitutions (longest first, already ordered above)
        for kanji_form, hira_form in _TEXT_PRE_FIXES:
            text = text.replace(kanji_form, hira_form)

        # 2. Expand 々 by replacing with the preceding kanji character,
        #    so pykakasi reads the doubled kanji correctly (燦々→燦燦→さんさん).
        expanded = []
        for ch in text:
            if ch == "々" and expanded:
                j = len(expanded) - 1
                while j >= 0 and expanded[j] == "々":
                    j -= 1
                if j >= 0:
                    expanded.append(expanded[j])
                    continue
            expanded.append(ch)
        text = "".join(expanded)

        # 3. Token-level overrides for single-kanji misreadings
        parts = []
        for item in kks.convert(text):
            orig = item.get("orig", "")
            if orig in _WORD_OVERRIDES:
                parts.append(_WORD_OVERRIDES[orig])
            else:
                parts.append(item.get("hira") or item.get("orig") or "")
        return "".join(parts)

    _CON_DUR = 0.055        # 55ms max consonant — allows natural variation on slow notes
    _MIN_SP = 0.040         # minimum silence gap to insert AP/SP between segments
    _MAX_RATIO = 4.5        # note/mora ratio beyond which we consider AMT unreliable for this line

    all_intervals: list[tuple[float, float, str]] = []
    failed = 0
    total_content = 0
    prev_seg_end = 0.0  # hard lower bound — prevents note double-counting at boundaries

    for seg in lyric_segs:
        t_start = float(seg["start"])
        t_end = float(seg["end"])
        text = seg.get("text", "").strip()
        if not text:
            continue

        hira = _kakasi_to_hira(text)
        all_morae = _split_morae(hira)
        # ー extends previous mora's vowel — doesn't need its own note slot
        content_morae = [m for m in all_morae if m != "ー"]
        if not content_morae:
            continue

        total_content += 1
        # Window: [lower, t_end) — strict upper bound so each note belongs to exactly one
        # lyric segment.  Lower is clamped to prev segment's end to prevent double-counting
        # at boundaries.  No end-tolerance: t_end == t_start of the next segment, so
        # any note that onsets at t_end naturally lands in the next segment's window.
        lower = max(prev_seg_end, t_start - 0.050)  # ≤50ms lookback only
        prev_seg_end = t_end
        seg_notes = [
            n for n in midi_notes
            if n[0] >= lower and n[0] < t_end
        ]

        # De-overlap raw AMT notes BEFORE reconcile (only if there are real notes).
        # Basic Pitch is a polyphonic detector and emits overlapping notes; reconcile
        # (split/merge) must see the final non-overlapping count to avoid losing morae.
        # Two cases:
        #   1. Same-onset (within 20ms): harmonic/chord duplicate → keep only the longer note.
        #   2. Staggered overlap: advance current note's start to previous note's end.
        #      Discard current note if the remaining duration < 20ms.
        if seg_notes:
            seg_notes.sort(key=lambda n: n[0])
            deoverlapped: list[tuple[float, float, float]] = []
            for note in seg_notes:
                if not deoverlapped:
                    deoverlapped.append(note)
                    continue
                prev = deoverlapped[-1]
                if note[0] < prev[1]:  # overlap
                    if note[0] - prev[0] <= 0.020:  # same-onset polyphony — keep longer
                        if (note[1] - note[0]) > (prev[1] - prev[0]):
                            deoverlapped[-1] = note  # replace with longer note
                        # else discard current note
                    else:  # staggered overlap — advance current start to prev's end
                        new_start = prev[1]
                        if note[1] - new_start >= 0.020:
                            deoverlapped.append((new_start, note[1], note[2]))
                        # else too short after advancement, discard
                else:
                    deoverlapped.append(note)
            seg_notes = deoverlapped

        n_notes = len(seg_notes)
        n_morae = len(content_morae)
        ratio = n_notes / n_morae if n_notes > 0 else 0.0

        # Even-distribution fallback: when AMT has no notes or ratio is out of range,
        # divide the lyric window evenly across morae instead of inserting silence.
        # Produces real (if metrically imprecise) singing for failed lines rather than
        # a gap. The F0 still comes from the original vocal audio via parselmouth.
        if n_notes == 0 or ratio < 0.25 or ratio > _MAX_RATIO:
            failed += 1
            _dur_each = (t_end - t_start) / max(1, n_morae)
            seg_notes = [
                (t_start + i * _dur_each, t_start + (i + 1) * _dur_each, 280.0)
                for i in range(n_morae)
            ]
            n_notes = n_morae
            ratio = 1.0

        # Reconcile note count to mora count
        if n_notes > n_morae:
            seg_notes = _merge_notes_to_count(seg_notes, n_morae)
        elif n_notes < n_morae:
            seg_notes = _split_notes_to_count(seg_notes, n_morae)

        # Re-sort after reconcile (split creates sub-intervals in place; merge is stable)
        seg_notes.sort(key=lambda n: n[0])

        # Close legato gaps: always extend each note to the next note's onset within
        # a lyric line.  All notes in seg_notes are within the same lyric line window,
        # so any gap between them is an AMT detection gap (inter-clause transitions,
        # missed onsets) rather than an intentional rest.  Previously 400ms threshold
        # left gaps of 400ms-1.14s un-closed → SP intervals → flush → 400-1140ms holes
        # in the DS chart → "laggy wifi" silence artifacts in output.
        _LEGATO_GAP = 0.400  # used only for trailing gap below
        legato: list[tuple[float, float, float]] = []
        for j, note in enumerate(seg_notes[:-1]):
            nxt = seg_notes[j + 1]
            legato.append((note[0], nxt[0], note[2]))  # always extend to next onset
        if seg_notes:
            last = seg_notes[-1]
            trailing = t_end - last[1]
            # Extend last note to t_end when the trailing gap is small (< 400ms).
            # Without this, the segment's audio ends before t_end and DiffSinger
            # inserts silence for the gap → audible millisecond cutout at phrase ends.
            # Large trailing gaps (≥ 400ms) are intentional pauses; leave them alone.
            if 0 < trailing < _LEGATO_GAP:
                legato.append((last[0], t_end, last[2]))
            else:
                legato.append(last)
        seg_notes = legato

        # Leading silence from segment start to first note
        if seg_notes[0][0] - t_start > _MIN_SP:
            all_intervals.append((t_start, seg_notes[0][0], "AP"))

        for mora, note in zip(content_morae, seg_notes):
            onset_s, offset_s, _pitch = note
            dur = offset_s - onset_s
            phones = _MORA_TO_IPA.get(mora)
            if phones is None:
                all_intervals.append((onset_s, offset_s, "a"))
                continue
            if not phones:
                if all_intervals and all_intervals[-1][2] not in ("AP", "SP"):
                    prev = all_intervals[-1]
                    all_intervals[-1] = (prev[0], offset_s, prev[2])
                continue
            if len(phones) == 1:
                all_intervals.append((onset_s, offset_s, phones[0]))
            else:
                # CV: consonant gets 28% of note duration (min 20ms, max 55ms).
                # 28% matches natural Japanese singing C/V ratio (≈1:3).
                # For short notes: guarantee the vowel at least matches the consonant
                # (min 50/50 split) so vowels are never shorter than consonants.
                con_dur = min(_CON_DUR, max(0.020, dur * 0.28))
                # Ensure vowel ≥ consonant: reduces consonant if note is too short.
                vow_dur = dur - con_dur
                if vow_dur < con_dur:
                    con_dur = dur / 2.0
                vow_start = onset_s + con_dur
                if vow_start < offset_s - 0.010:
                    all_intervals.append((onset_s, vow_start, phones[0]))
                    all_intervals.append((vow_start, offset_s, phones[1]))
                else:
                    all_intervals.append((onset_s, offset_s, phones[-1]))

        # Trailing silence from last note to segment end (only for real phrase gaps)
        if seg_notes and t_end - seg_notes[-1][1] > _MIN_SP:
            all_intervals.append((seg_notes[-1][1], t_end, "SP"))

    if total_content > 0 and failed / total_content > 0.40:
        raise RuntimeError(
            f"AMT alignment failed on {failed}/{total_content} segments "
            f"({failed/total_content*100:.0f}%) — falling back to SOFA"
        )
    if failed > 0:
        print(f"  AMT: {failed}/{total_content} segments used SP placeholder")

    all_intervals.sort(key=lambda x: x[0])

    # Fill inter-segment gaps with SP (only actual phrase boundaries, not legato gaps)
    filled: list[tuple[float, float, str]] = []
    for s, e, ph in all_intervals:
        if filled and s - filled[-1][1] > _MIN_SP:
            filled.append((filled[-1][1], s, "SP"))
        filled.append((s, e, ph))

    print(f"  AMT alignment: {len(filled)} intervals from {total_content} lyric lines")
    return filled


_SILENCE_SET = frozenset({"AP", "SP"})
_VOWELS_CHECK = frozenset({"a", "i", "ɯ", "e", "o", "ɴ"})


def _split_long_vowels_by_rms(
    intervals: list[tuple[float, float, str]],
    vocals_audio: np.ndarray,
    sr: int,
    max_vowel_ms: float = 600.0,
    rms_win_ms: float = 15.0,
    silence_ratio: float = 0.20,
    min_voiced_ms: float = 80.0,
    min_sp_ms: float = 100.0,
) -> list[tuple[float, float, str]]:
    """
    For each vowel longer than max_vowel_ms, compute RMS energy to find where
    the singer actually stops vocalizing (energy drops below silence_ratio of
    the interval's peak). Split there: phone [start→drop], SP [drop→end].

    This corrects SOFA's tendency to extend phrase-final vowels into the
    inter-phrase gap, which causes (a) missing pauses and (b) false rising-pitch
    glides in DiffSinger output from linear F0 interpolation across silence.
    """
    result = list(intervals)
    rms_win = max(1, int(rms_win_ms / 1000 * sr))
    min_voiced_win = max(2, int(min_voiced_ms / 1000 * sr) // rms_win)
    min_sp_samples = int(min_sp_ms / 1000 * sr)
    changed = 0

    j = 0
    while j < len(result):
        xmin, xmax, label = result[j]
        if label not in _VOWELS_CHECK or (xmax - xmin) * 1000 <= max_vowel_ms:
            j += 1
            continue

        s = int(xmin * sr)
        e = int(min(xmax * sr, len(vocals_audio)))
        chunk = vocals_audio[s:e]
        if len(chunk) < rms_win * (min_voiced_win + 2):
            j += 1
            continue

        n_win = len(chunk) // rms_win
        rms = np.array([
            np.sqrt(np.mean(chunk[k * rms_win:(k + 1) * rms_win] ** 2))
            for k in range(n_win)
        ])
        rms_max = rms.max()
        if rms_max < 1e-7:
            j += 1
            continue

        threshold = silence_ratio * rms_max
        split_win = None
        for w in range(min_voiced_win, n_win):
            if rms[w] < threshold:
                split_win = w
                break

        if split_win is None:
            j += 1
            continue

        split_time = xmin + split_win * rms_win / sr
        voiced_dur = split_time - xmin
        sp_dur = xmax - split_time

        if voiced_dur < min_voiced_ms / 1000 or sp_dur * sr < min_sp_samples:
            j += 1
            continue

        result[j] = (xmin, split_time, label)
        result.insert(j + 1, (split_time, xmax, "SP"))
        changed += 1
        j += 2

    if changed:
        print(f"  RMS-split {changed} long vowels at silence boundary (phrase gap correction)")
    return result


def _apply_whisper_boundaries(
    intervals: list[tuple[float, float, str]],
    whisper_segs: list[dict],
    min_gap: float = 0.05,
) -> list[tuple[float, float, str]]:
    """
    Post-process SOFA TextGrid to insert SP at Whisper phrase boundaries.

    SOFA ignores SP tokens in the input lab (they're not in its mora vocabulary),
    so it stretches the final vowel of each phrase into the inter-phrase gap.
    This function corrects that by:
      1. For each Whisper gap >= min_gap, take the segment[i].end timestamp
         (≈ when the singer stops vocalizing that phrase)
      2. Find which TextGrid interval spans that timestamp
      3. If it's a vowel, split it: phone [xmin→gap_start] + SP [gap_start→xmax]
    The intervals remain continuous and the SP duration = what SOFA wrongly
    attributed to the preceding vowel.
    """
    result = list(intervals)

    # Collect gap_start timestamps where meaningful pauses occur (last→first to preserve indices)
    gap_starts = []
    for i in range(len(whisper_segs) - 1):
        gap_start = whisper_segs[i]["end"]
        gap_end = whisper_segs[i + 1]["start"]
        if gap_end - gap_start >= min_gap:
            gap_starts.append(gap_start)

    inserted = 0
    for gap_start in reversed(gap_starts):
        for j, (xmin, xmax, label) in enumerate(result):
            if xmin >= gap_start:
                break  # past the boundary, not found
            if xmax <= gap_start:
                continue
            # Interval (xmin, xmax) contains gap_start
            if label in _SILENCE_SET:
                break  # already a silence — no action needed
            # It's a phone (vowel or consonant) spanning the phrase boundary.
            # Only split if enough of the phone precedes the boundary (10ms min)
            # and the trailing portion is meaningful (20ms min → becomes SP).
            if gap_start - xmin >= 0.01 and xmax - gap_start >= 0.02:
                result[j] = (xmin, gap_start, label)
                result.insert(j + 1, (gap_start, xmax, "SP"))
                inserted += 1
            break

    if inserted:
        print(f"  Applied {inserted} Whisper boundary corrections to SOFA alignment")
    return result


# ---------------------------------------------------------------------------
# MFA aligner (fallback — speech-trained, may mark fast singing as silence)
# ---------------------------------------------------------------------------

def _run_mfa(vocals_path: str, transcript: str, work_dir: str) -> str:
    print("  Running MFA alignment...")
    mfa_in = Path(work_dir) / "mfa_in"
    mfa_out = Path(work_dir) / "mfa_out"
    mfa_in.mkdir(exist_ok=True)
    mfa_out.mkdir(exist_ok=True)

    audio, sr = sf.read(vocals_path)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio_16k = _resample(audio.astype(np.float32), sr, _MFA_SR)
    mfa_wav = mfa_in / "song.wav"
    sf.write(str(mfa_wav), audio_16k, _MFA_SR, subtype="PCM_16")
    (mfa_in / "song.lab").write_text(transcript, encoding="utf-8")

    def _mfa_run(extra_args: list[str]) -> subprocess.CompletedProcess:
        cmd = ["conda", "run", "-n", "mfa", "mfa", "align",
               str(mfa_in), "japanese_mfa", "japanese_mfa", str(mfa_out),
               "--clean", "--single_speaker", "--quiet"] + extra_args
        return subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    result = _mfa_run(["--beam", "50"])
    tg = mfa_out / "song.TextGrid"
    if not tg.exists():
        err_snippet = (result.stderr or "")[-600:].strip()
        if err_snippet:
            print(f"  MFA first-pass stderr: {err_snippet[:300]}")
        print("  MFA first pass failed, retrying with larger beam...")
        result = _mfa_run(["--beam", "100", "--retry_beam", "400"])
    if not tg.exists():
        raise RuntimeError(f"MFA produced no TextGrid.\nstderr: {result.stderr[-500:]}")
    return str(tg)


def _parse_textgrid(path: str) -> list[tuple[float, float, str]]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    tier_blocks = re.split(r'item\s*\[\d+\]\s*:', text)
    for block in tier_blocks:
        if 'name = "phones"' not in block:
            continue
        intervals = []
        for ib in re.split(r'intervals\s*\[\d+\]\s*:', block)[1:]:
            xm = re.search(r'xmin\s*=\s*([\d.eE+\-]+)', ib)
            xM = re.search(r'xmax\s*=\s*([\d.eE+\-]+)', ib)
            tm = re.search(r'text\s*=\s*"([^"]*)"', ib)
            if xm and xM and tm:
                intervals.append((float(xm.group(1)), float(xM.group(1)), tm.group(1).strip()))
        return intervals
    return []


_SILENCE_LABELS = {"", "sil", "spn", "sp", "SIL", "SPN", "SP"}

# Load the 63-phoneme set (matches the koroki_v12 model vocab) to normalize OOV phonemes.
# CRITICAL: if this file is missing, _KNOWN_PHONES collapses to {AP,SP} and EVERY phoneme
# maps to SP -> the build produces 0 segments. That silent failure cost hours of debugging
# (the old path data/diffsinger_raw/japanese/phonemes.txt was deleted in a cleanup). Keep a
# stable copy next to this script; fall back to the v12 training set; warn LOUDLY if empty.
_PH_SET_PATH = _SELF_DIR / "phonemes_63.txt"
if not _PH_SET_PATH.exists():
    for _alt in (_REPO_ROOT / "data/diffsinger_raw/ikura_real/phonemes.txt",
                 _REPO_ROOT / "data/diffsinger_raw/koroki_singing_v5/yoasobi/phonemes.txt"):
        if _alt.exists():
            _PH_SET_PATH = _alt
            break
if _PH_SET_PATH.exists():
    _KNOWN_PHONES: set[str] = set(_PH_SET_PATH.read_text(encoding="utf-8").split()) | {"AP", "SP"}
else:
    print("*** WARNING: no phonemes.txt found -> _KNOWN_PHONES empty; builds WILL fail ***")
    _KNOWN_PHONES = {"AP", "SP"}


def _normalize_phone(ph: str) -> str:
    """Map OOV phonemes to closest known phoneme."""
    if ph in _KNOWN_PHONES:
        return ph
    # Strip length mark ː and try again
    base = ph.replace("ː", "")
    if base in _KNOWN_PHONES:
        return base
    # Strip palatalization ʲ
    base2 = base.replace("ʲ", "")
    if base2 in _KNOWN_PHONES:
        return base2
    # Take first character
    base3 = ph[0] if ph else "SP"
    if base3 in _KNOWN_PHONES:
        return base3
    print(f"  WARN: unknown phoneme '{ph}' → SP")
    return "SP"


def _build_ds_segments(intervals: list[tuple[float, float, str]],
                       vocals_audio: np.ndarray, sr: int,
                       lyric_boundaries: list[float] | None = None) -> list[dict]:
    segments = []
    current_phones: list[tuple[float, float, str]] = []
    seg_start = None

    dropped = [0]

    def _flush():
        if not current_phones:
            return
        start = current_phones[0][0]
        end = current_phones[-1][1]
        dur = end - start
        if dur < _MIN_SEG_DUR:
            dropped[0] += 1
            return

        ph_seq, ph_dur = [], []
        _MIN_VOICED_DUR = 0.020  # 20ms (4 frames @ 5ms) — minimum DiffSinger can render; 40ms was
        # collapsing all consonants to identical 40ms, crushing vowels in fast passages
        first = True
        for (xmin, xmax, label) in current_phones:
            d = round(xmax - xmin, 4)
            if d < 0.003:  # truly zero-duration, skip
                continue
            ph = "AP" if (label in _SILENCE_LABELS and first) else (
                "SP" if label in _SILENCE_LABELS else _normalize_phone(label))
            first = False
            if ph not in ("AP", "SP") and d < _MIN_VOICED_DUR:
                d = _MIN_VOICED_DUR  # bump short consonants/vowels to minimum
            if ph in ("AP", "SP") and ph_seq and ph_seq[-1] == ph:
                ph_dur[-1] += d
            else:
                ph_seq.append(ph)
                ph_dur.append(d)

        if not ph_seq or all(p in ("AP", "SP") for p in ph_seq):
            return

        real_phones = [p for p in ph_seq if p not in ("AP", "SP")]
        real_dur = sum(d for p, d in zip(ph_seq, ph_dur) if p not in ("AP", "SP"))
        if real_phones:
            avg_ms = real_dur / len(real_phones) * 1000
            total_dur = sum(ph_dur)

            # Too compressed: MFA forced too many phonemes into too little time.
            # Scale real phonemes up toward 80ms each, shrink SP to compensate.
            # Total duration is kept equal to the segment's actual song span.
            if avg_ms < 40:
                sp_dur = total_dur - real_dur
                sp_count = sum(1 for p in ph_seq if p in ("AP", "SP"))
                min_sp_dur = sp_count * 0.02  # 20ms floor per SP phoneme
                available_for_real = total_dur - min_sp_dur
                target_real = min(len(real_phones) * 0.08, available_for_real)
                scale_real = target_real / real_dur if real_dur > 0 else 1.0
                remaining_sp = max(min_sp_dur, total_dur - target_real)
                scale_sp = remaining_sp / sp_dur if sp_dur > 0 else 1.0
                ph_dur = [
                    round(d * scale_real, 4) if p not in ("AP", "SP") else round(d * scale_sp, 4)
                    for p, d in zip(ph_seq, ph_dur)
                ]
                avg_ms_new = sum(d for p, d in zip(ph_seq, ph_dur) if p not in ("AP", "SP")) / len(real_phones) * 1000
                print(f"  SCALE seg @{start:.2f}s: avg {avg_ms:.0f}ms → {avg_ms_new:.0f}ms/ph (kept)")

        # Cap individual vowel/nasal durations.
        # SOFA correctly detects held notes (e.g. "な" held 2.83s in slow dramatic sections).
        # DiffSinger can hold vowels/nasals fine. Cap only prevents runaway values from bad
        # SOFA alignment; 3.0s covers all realistic Japanese singing phrases.
        # NOTE: 1.2s was tried and reverted — it clipped legitimate long holds in slow bridges.
        # 0.7s was tried earlier for the same reason and caused audible gaps.
        _VOWELS_IPA = frozenset({"a", "i", "ɯ", "e", "o", "ɴ"})
        _MAX_VOWEL_DUR = 3.0
        _MAX_NASAL_DUR = 3.0
        idx = 0
        while idx < len(ph_seq):
            if ph_seq[idx] in _VOWELS_IPA:
                cap = _MAX_NASAL_DUR if ph_seq[idx] == "ɴ" else _MAX_VOWEL_DUR
                if ph_dur[idx] > cap:
                    ph_dur[idx] = cap  # excess dropped; normaliser redistributes
            idx += 1

        # Cap interior AP/SP durations to prevent long mid-phrase silences.
        # SOFA sometimes inserts multi-second AP/SP tokens at formant transitions;
        # DiffSinger renders those as literal silence. Short ones (<=150ms) are natural
        # breaths and must stay — they're required for correct phrase phrasing.
        _MAX_INTERIOR_AP = 0.15
        first_v = next((k for k, p in enumerate(ph_seq) if p not in ("AP", "SP")), None)
        last_v  = max((k for k, p in enumerate(ph_seq) if p not in ("AP", "SP")), default=None)
        if first_v is not None and last_v is not None and first_v < last_v:
            for k in range(first_v + 1, last_v):
                if ph_seq[k] in ("AP", "SP") and ph_dur[k] > _MAX_INTERIOR_AP:
                    print(f"  CAP interior {ph_seq[k]} @{start:.2f}s idx{k}: {ph_dur[k]:.3f}s → {_MAX_INTERIOR_AP}s")
                    ph_dur[k] = _MAX_INTERIOR_AP

        # Cap leading and trailing AP/SP durations.
        # SOFA sometimes assigns 0.5–1.5s to the opening breath or closing pause —
        # DiffSinger renders those frames as literal silence, so the listener hears
        # Koroki not singing for over a second at the start/end of every phrase.
        # Cap both to 150ms and push the excess into the adjacent voiced phoneme.
        _MAX_EDGE_AP = 0.15
        # Cap leading/trailing AP/SP — just drop excess, never push onto consonants.
        # Pushing onto the first/last phoneme (usually a consonant) inflates it to
        # 1-2s which DiffSinger cannot synthesize, producing silence.  The normaliser
        # below only scales DOWN so the segment will simply end slightly early.
        if ph_seq and ph_seq[0] in ("AP", "SP") and ph_dur[0] > _MAX_EDGE_AP:
            ph_dur[0] = _MAX_EDGE_AP
        if ph_seq and ph_seq[-1] in ("AP", "SP") and ph_dur[-1] > _MAX_EDGE_AP:
            ph_dur[-1] = _MAX_EDGE_AP

        # Only scale DOWN if phoneme durations exceed the segment's audio window.
        # Scaling up (when caps shortened the total) would inflate consonants to
        # 1-2s durations that DiffSinger cannot synthesize, producing silence.
        # If total < dur the segment simply ends early — silence fills the remainder.
        #
        # Vowel-first scale-down: absorb excess duration from vowels/silences first,
        # preserving consonants at their (proportional) durations.
        # Absorb excess from vowels/nasals/silences first; fall back to uniform if needed.
        current_total = sum(ph_dur)
        if current_total > 0 and current_total > dur + 0.02:
            excess = current_total - dur
            _SOFT_PH = _VOWELS_IPA | {"AP", "SP"}
            soft_dur = sum(d for p, d in zip(ph_seq, ph_dur) if p in _SOFT_PH)
            if soft_dur > excess + 0.01:
                soft_scale = (soft_dur - excess) / soft_dur
                ph_dur = [
                    round(d * soft_scale, 4) if p in _SOFT_PH else d
                    for p, d in zip(ph_seq, ph_dur)
                ]
            else:
                adj = dur / current_total
                ph_dur = [round(d * adj, 4) for d in ph_dur]

        s_sample = int(start * sr)
        e_sample = int(end * sr)
        clip = vocals_audio[s_sample:e_sample]
        # Use the final ph_dur sum — not the original audio window — so F0 length
        # matches exactly what DiffSinger receives.  Caps and scale-down above can
        # reduce sum(ph_dur) below dur; extracting F0 for dur left orphaned frames
        # that DiffSinger would truncate or misalign against phoneme boundaries.
        actual_dur = sum(ph_dur)
        f0, voiced_frac = _extract_f0(clip, sr, actual_dur)

        # Skip segments where the vocal track has no signal — demucs separation failure
        # or truly instrumental sections. Synthesizing with unvoiced F0 produces loud
        # robotic noise. 5% threshold keeps soft/whispered vocals while dropping dead regions.
        if voiced_frac < 0.05:
            print(f"  WARN: segment at {start:.2f}s skipped — only {voiced_frac*100:.1f}% voiced frames (no vocal signal)")
            return

        f0 = _ramp_f0_before_silence(f0, ph_seq, ph_dur)

        f0_dur = len(f0) * _F0_TIMESTEP
        ph_dur_sum = sum(ph_dur)
        if abs(ph_dur_sum - f0_dur) > 0.020:
            print(f"  WARN: seg @{start:.2f}s F0/ph_dur mismatch: ph={ph_dur_sum:.3f}s f0={f0_dur:.3f}s diff={abs(ph_dur_sum-f0_dur)*1000:.0f}ms")

        avg_phone_ms = real_dur / len(real_phones) * 1000 if real_phones else 100.0
        segments.append({
            "offset": round(start, 4),
            "text": " ".join(ph_seq),
            "ph_seq": " ".join(ph_seq),
            "ph_dur": " ".join(str(round(d, 4)) for d in ph_dur),
            "f0_seq": " ".join(str(v) for v in f0),
            "f0_timestep": str(_F0_TIMESTEP),
            "avg_phone_ms": round(avg_phone_ms, 1),
        })

    sorted_bounds = sorted(lyric_boundaries) if lyric_boundaries else []
    bound_idx = 0

    for xmin, xmax, label in intervals:
        # Force-flush at lyric line boundaries (sentence-per-sentence mode).
        # Synced lyrics give us per-line timestamps; use them as hard split points
        # so DiffSinger always receives sentence-length inputs rather than 13-15s
        # chorus blobs that degrade attention.
        if sorted_bounds and bound_idx < len(sorted_bounds):
            while bound_idx < len(sorted_bounds) and sorted_bounds[bound_idx] <= xmin:
                if current_phones:
                    _flush()
                    current_phones = []
                    seg_start = None
                bound_idx += 1

        dur = xmax - xmin
        is_sil = label in _SILENCE_LABELS
        # Split on silence gaps (natural phrase boundaries)
        if is_sil and dur >= _SILENCE_THRESH and current_phones:
            _flush()
            current_phones = []
            seg_start = None
        else:
            if seg_start is None:
                seg_start = xmin
            current_phones.append((xmin, xmax, label))
            # Force-split if segment grows too long — DiffSinger attention degrades
            # on segments > ~15s. Prefer cutting at the last SP (clean phrase boundary)
            # rather than mid-phoneme so _flush receives a coherent phoneme sequence.
            if seg_start is not None and xmax - seg_start >= _MAX_SEG_DUR:
                split_at = None
                for k in range(len(current_phones) - 2, -1, -1):
                    if current_phones[k][2] in _SILENCE_LABELS:
                        if current_phones[k][1] - seg_start >= 3.0:  # first half ≥ 3s
                            split_at = k + 1  # remainder starts after the SP
                            break
                if split_at is not None and split_at < len(current_phones):
                    remainder = current_phones[split_at:]
                    current_phones = current_phones[:split_at]
                    half1 = current_phones[-1][1] - seg_start
                    half2 = remainder[-1][1] - remainder[0][0] if remainder else 0
                    print(f"  SPLIT @{seg_start:.1f}s: {half1:.1f}s + {half2:.1f}s (at last SP)")
                    _flush()
                    current_phones = remainder
                    seg_start = remainder[0][0] if remainder else None
                else:
                    # Try 2: RMS quiet-point — find the quietest 50ms audio window in
                    # the middle third of the segment and snap to the nearest phoneme boundary.
                    # This avoids cutting mid-syllable in fast chorus sections where SOFA
                    # produces no SP at all (every phoneme is voiced with no silence gap).
                    mid_s = seg_start + (xmax - seg_start) / 3
                    mid_e = seg_start + (xmax - seg_start) * 2 / 3
                    rms_win = max(1, int(0.05 * sr))  # 50 ms windows
                    chunk = vocals_audio[int(mid_s * sr):min(int(mid_e * sr), len(vocals_audio))]
                    rms_split_at = None
                    if len(chunk) >= rms_win * 2:
                        n_w = len(chunk) // rms_win
                        rms = np.array([
                            np.sqrt(np.mean(chunk[k * rms_win:(k + 1) * rms_win] ** 2))
                            for k in range(n_w)
                        ])
                        quiet_time = mid_s + int(np.argmin(rms)) * 0.05
                        best_idx, best_dist = None, 0.3  # 300 ms snap tolerance
                        for k in range(len(current_phones) - 1):
                            dist = abs(current_phones[k][1] - quiet_time)
                            if dist < best_dist:
                                best_dist = dist
                                best_idx = k + 1
                        if best_idx and 0 < best_idx < len(current_phones):
                            if current_phones[best_idx - 1][1] - seg_start >= _MIN_SEG_DUR:
                                rms_split_at = best_idx
                    if rms_split_at is not None:
                        remainder = current_phones[rms_split_at:]
                        current_phones = current_phones[:rms_split_at]
                        half1 = current_phones[-1][1] - seg_start
                        half2 = remainder[-1][1] - remainder[0][0] if remainder else 0
                        print(f"  SPLIT @{seg_start:.1f}s: {half1:.1f}s + {half2:.1f}s (RMS quiet)")
                        _flush()
                        current_phones = remainder
                        seg_start = remainder[0][0] if remainder else None
                    else:
                        print(f"  SPLIT @{seg_start:.1f}s: hard cut ({xmax - seg_start:.1f}s, no SP or quiet point)")
                        _flush()
                        current_phones = []
                        seg_start = None

    _flush()
    if dropped[0]:
        print(f"  WARN: dropped {dropped[0]} segments shorter than {_MIN_SEG_DUR}s")
    return segments


# ---------------------------------------------------------------------------
# Mix + output
# ---------------------------------------------------------------------------

def _find_instr_offset(demucs_instr_path: str, official_instr_path: str) -> float:
    """Cross-correlate RMS energy envelopes of two instrumentals and return the
    time offset (seconds) to apply to the official instrumental.
    Positive → official is behind (prepend silence). Negative → official is ahead (skip samples).
    Raw-sample cross-correlation is unreliable on music because periodic phrases create
    spurious peaks; RMS envelopes (50 ms windows) are much more robust.
    Result is clamped to ±3 s — larger values almost certainly indicate a false peak.
    """
    from scipy.signal import correlate
    _MAX_OFFSET = 3.0
    ref, r_sr = sf.read(demucs_instr_path)
    off, o_sr = sf.read(official_instr_path)
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    if off.ndim > 1:
        off = off.mean(axis=1)
    if r_sr != o_sr:
        off = _resample(off.astype(np.float32), o_sr, r_sr)
    n = min(len(ref), len(off), int(30 * r_sr))
    ref = ref[:n].astype(np.float32)
    off = off[:n].astype(np.float32)
    # Compute RMS in 50 ms windows to make correlation periodic-music-proof
    win = max(1, int(0.05 * r_sr))
    n_frames = min(len(ref), len(off)) // win
    ref_env = np.array([np.sqrt(np.mean(ref[i * win:(i + 1) * win] ** 2)) for i in range(n_frames)])
    off_env = np.array([np.sqrt(np.mean(off[i * win:(i + 1) * win] ** 2)) for i in range(n_frames)])
    ref_env /= (ref_env.max() + 1e-8)
    off_env /= (off_env.max() + 1e-8)
    corr = correlate(ref_env, off_env, mode='full')
    lag_frames = int(np.argmax(corr)) - (n_frames - 1)
    offset_s = lag_frames * win / r_sr
    if abs(offset_s) > _MAX_OFFSET:
        print(f"  WARN: detected instrumental offset {offset_s:+.3f}s exceeds {_MAX_OFFSET}s limit — likely a false peak, skipping")
        return 0.0
    return offset_s


def _pitch_shift_wav(input_path: str, output_path: str, semitones: float) -> None:
    """Pitch-shift a WAV by N semitones without changing duration.
    Uses FFmpeg asetrate+atempo chain: asetrate shifts pitch+speed, atempo corrects speed."""
    ratio = 2 ** (semitones / 12)
    new_rate = int(44100 * ratio)
    atempo = 1.0 / ratio
    filt = f"asetrate={new_rate},aresample=44100,atempo={atempo:.6f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", filt, output_path]
    result = subprocess.run(cmd, capture_output=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg pitch shift failed: {result.stderr[-400:]}")


# ── Chain helpers: variance extraction + RVC post-pass (the koroki_v12 chain) ─
# THE CHAIN (see LEGACY 2026-06-27): DiffSinger v12 (clean real-Ikura voice — handles
# gender + arbitrary notes via full synthesis) -> RVC Korokiv5 (Koroki timbre + presence;
# re-vocoding also launders DiffSinger's vocoder huskiness). DiffSinger no longer needs to
# BE Koroki — RVC does that. v12 uses variance embeds, so the .ds needs energy/breathiness/
# voicing curves; we extract them from the source vocal (contours only -> gender-neutral).
def _add_source_variances(segments: list, vocal_path: str) -> None:
    """Extract energy/breathiness/voicing from the source vocal per segment into the .ds."""
    sys.path.insert(0, str(_DIFFSINGER_DIR))
    from utils.binarizer_utils import (get_energy_librosa, get_breathiness, get_voicing,
                                        SinusoidalSmoothingConv1d, DecomposedWaveform)
    import torch
    SR, HOP, WIN, FFT = 44100, 512, 2048, 2048
    TS = HOP / SR; KERNEL = round(0.12 / TS)
    v, sr0 = sf.read(vocal_path)
    if v.ndim > 1:
        v = v.mean(axis=1)
    if sr0 != SR:
        import librosa
        v = librosa.resample(v.astype(np.float32), orig_sr=sr0, target_sr=SR)
    v = v.astype(np.float32)
    smooth = SinusoidalSmoothingConv1d(KERNEL).eval()
    for seg in segments:
        offset = float(seg["offset"]); dur = sum(float(x) for x in seg["ph_dur"].split())
        a = v[int(offset * SR): int((offset + dur) * SR)]
        length = max(1, round(dur * SR / HOP))
        if len(a) < WIN:
            a = np.pad(a, (0, WIN - len(a)))
        f0 = np.array(seg["f0_seq"].split(), np.float32)
        ot = np.arange(len(f0)) * float(seg["f0_timestep"]); nt = np.arange(length) * TS
        f0 = np.interp(nt, ot, f0).astype(np.float32); uv = f0 <= 0
        en = smooth(torch.from_numpy(get_energy_librosa(a, length, hop_size=HOP, win_size=WIN).astype(np.float32))[None])[0].detach().numpy()
        dec = DecomposedWaveform(a, samplerate=SR, f0=f0 * ~uv, hop_size=HOP, fft_size=FFT, win_size=WIN, algorithm="world")
        br = smooth(torch.from_numpy(get_breathiness(dec, None, None, length=length).astype(np.float32))[None])[0].detach().numpy()
        vo = smooth(torch.from_numpy(get_voicing(dec, None, None, length=length).astype(np.float32))[None])[0].detach().numpy()
        seg["energy"] = " ".join(f"{x:.4f}" for x in en); seg["energy_timestep"] = TS
        seg["breathiness"] = " ".join(f"{x:.4f}" for x in br); seg["breathiness_timestep"] = TS
        seg["voicing"] = " ".join(f"{x:.4f}" for x in vo); seg["voicing_timestep"] = TS


def _add_flat_variances(entries: list) -> None:
    """Flat default variance for rap mini-chunks (their stretched/split timing makes
    per-frame source extraction unreliable; rap dynamics are minor)."""
    SR, HOP = 44100, 512; TS = HOP / SR
    for e in entries:
        dur = sum(float(x) for x in e["ph_dur"].split())
        length = max(1, round(dur * SR / HOP))
        e["energy"] = " ".join(["-18.0"] * length); e["energy_timestep"] = TS
        e["breathiness"] = " ".join(["-45.0"] * length); e["breathiness_timestep"] = TS
        e["voicing"] = " ".join(["-18.0"] * length); e["voicing_timestep"] = TS


def _run_rvc_chain(wav_in: str, wav_out: str) -> bool:
    """RVC post-pass: convert the DiffSinger (Ikura-voice) output to Koroki via Korokiv5."""
    import tempfile as _tf
    applio = _REPO_ROOT / "ApplioV3.6.2"
    pth = _REPO_ROOT / "adapters/singing/Korokiv5_300e_34500s_best_epoch.pth"
    idx = _REPO_ROOT / "adapters/singing/Korokiv5.index"
    win = Path(wav_in).resolve()
    with _tf.NamedTemporaryFile(suffix=".wav", dir=str(applio), delete=False) as t:
        tmp = Path(t.name)
    cmd = [str(applio / "env/python.exe"), str(applio / "core.py"), "infer",
           "--input_path", str(win), "--output_path", str(tmp),
           "--pth_path", str(pth), "--index_path", str(idx),
           "--pitch", "0", "--f0_method", "rmvpe", "--index_rate", "0.4",
           "--volume_envelope", "1.0", "--protect", "0.33", "--split_audio", "True",
           "--f0_autotune", "False", "--f0_autotune_strength", "0.0",
           "--clean_audio", "False", "--clean_strength", "0.7", "--export_format", "WAV",
           "--embedder_model", "contentvec", "--formant_shifting", "False", "--post_process", "False"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(applio))
    found = tmp if (tmp.exists() and tmp.stat().st_size > 0) else None
    if found is None:
        cands = [p for p in applio.glob("*.wav") if p != tmp and p.stat().st_size > 0]
        found = max(cands, key=lambda p: p.stat().st_mtime) if cands else None
    if found is None:
        print(f"  RVC chain FAILED (rc={result.returncode}): {result.stderr[-300:]}")
        return False
    shutil.move(str(found), str(wav_out))
    return True


def _atempo_compress(audio: np.ndarray, sr: int, factor: float, tmp_dir: Path) -> np.ndarray:
    """Time-compress audio by factor using ffmpeg atempo (WSOLA — no phase-vocoder artifacts).
    factor > 1 = compress (speed up); e.g. 1.25 makes audio 1/1.25 of original length.
    atempo range is 0.5..2.0; values above 2.0 are chained automatically.
    Falls back to librosa if ffmpeg fails.
    """
    import tempfile
    uid = abs(hash(audio.tobytes()))
    tmp_in  = str(tmp_dir / f"_atmp_in_{uid}.wav")
    tmp_out = str(tmp_dir / f"_atmp_out_{uid}.wav")
    sf.write(tmp_in, audio, sr)
    # Build atempo chain — each stage capped at [0.5, 2.0]
    stages: list[float] = []
    remaining = factor
    while remaining > 2.0:
        stages.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        stages.append(0.5)
        remaining /= 0.5
    stages.append(remaining)
    af = ",".join(f"atempo={s:.6f}" for s in stages)
    cmd = ["ffmpeg", "-y", "-i", tmp_in, "-filter:a", af, "-ar", str(sr), tmp_out]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        # fallback: librosa phase vocoder (lower quality)
        import librosa as _lib
        return _lib.effects.time_stretch(audio, rate=factor)
    out, _ = sf.read(tmp_out)
    if out.ndim > 1:
        out = out.mean(axis=1)
    return out.astype(np.float32)


_MIN_PHONE_S = 0.045  # 45ms minimum per phoneme — below this DiffSinger loses confidence


def _has_repetitive_pattern(phones: list[str], min_n: int = 2, min_reps: int = 2) -> bool:
    """Return True if phones contains a consecutive repeating n-gram (n>=min_n, reps>=min_reps).
    E.g. [n,a,i,n,a,i,n,a,i] → True (n=3, reps=3).
    Used to detect 'ないないない'-style sections where DiffSinger loses confidence even
    at comfortable avg durations because the attention context is ambiguous.
    """
    n_ph = len(phones)
    for n in range(min_n, min(6, n_ph // 2 + 1)):
        for i in range(n_ph - n * min_reps + 1):
            gram = phones[i:i + n]
            count, j = 1, i + n
            while j + n <= n_ph and phones[j:j + n] == gram:
                count += 1
                j += n
            if count >= min_reps:
                return True
    return False


def _stretch_rap_segment(seg: dict, factor: float) -> tuple[dict, float]:
    """Stretch ph_dur by factor with a per-phoneme minimum floor.

    Uniform multiplication alone leaves short phonemes (20ms) at 25ms after 1.25x —
    still too short. Per-phoneme clamping ensures every phoneme >= _MIN_PHONE_S,
    then the actual compression ratio is derived from the real duration change so
    the time-compress step fits it back precisely.

    Returns (stretched_seg, actual_factor) where actual_factor is the real ratio
    of stretched_total / orig_total (may differ from `factor` due to clamping).
    """
    ph_seq = seg["ph_seq"].split()
    orig_durs = [float(d) for d in seg["ph_dur"].split()]

    stretched_durs = []
    for ph, d in zip(ph_seq, orig_durs):
        if ph in ("AP", "SP"):
            stretched_durs.append(round(d * factor, 4))
        else:
            stretched_durs.append(round(max(d * factor, _MIN_PHONE_S), 4))

    orig_total = sum(orig_durs)
    stretched_total = sum(stretched_durs)
    actual_factor = stretched_total / orig_total if orig_total > 0 else factor

    orig_f0 = [float(v) for v in seg["f0_seq"].split()]
    if orig_f0:
        orig_frames = len(orig_f0)
        new_frames = max(1, round(orig_frames * actual_factor))
        orig_x = np.linspace(0, 1, orig_frames)
        new_x = np.linspace(0, 1, new_frames)
        stretched_f0 = list(np.interp(new_x, orig_x, orig_f0))
    else:
        stretched_f0 = orig_f0

    out = dict(seg)
    out["ph_dur"] = " ".join(str(d) for d in stretched_durs)
    out["f0_seq"] = " ".join(f"{v:.2f}" for v in stretched_f0)
    return out, actual_factor


_REP_CHUNK_SIZE = 5  # max real phonemes per mini-chunk for repetitive sections


def _rep_boundary_groups(phones: list[str]) -> list[list[int]]:
    """Partition real-phoneme indices into groups that split at repetition boundaries.

    For [a ɾ e m o n a i n a i n a i], returns:
      [0,1,2,3,4], [5,6,7], [8,9,10], [11,12,13]
    — each repetition unit is its own group so no group contains a partial repeat.
    Non-repetitive runs are kept together (capped at _REP_CHUNK_SIZE).
    """
    n_ph = len(phones)
    # Find all repeating runs: (start, end_excl, unit_size)
    runs: list[tuple[int, int, int]] = []
    covered: set[int] = set()
    for n in range(2, min(6, n_ph // 2 + 1)):
        for i in range(n_ph - n * 2 + 1):
            if i in covered:
                continue
            gram = phones[i:i + n]
            j = i + n
            while j + n <= n_ph and phones[j:j + n] == gram:
                j += n
            reps = (j - i) // n
            if reps >= 2:
                runs.append((i, j, n))
                for k in range(i, j):
                    covered.add(k)
    runs.sort()

    groups: list[list[int]] = []
    pos = 0
    for (rstart, rend, unit_n) in runs:
        # Non-rep phonemes before this run → groups of max _REP_CHUNK_SIZE
        if pos < rstart:
            non_rep = list(range(pos, rstart))
            for cs in range(0, len(non_rep), _REP_CHUNK_SIZE):
                groups.append(non_rep[cs:cs + _REP_CHUNK_SIZE])
        # Each repetition unit → its own group
        for k in range(rstart, rend, unit_n):
            groups.append(list(range(k, k + unit_n)))
        pos = rend
    # Trailing non-rep
    if pos < n_ph:
        non_rep = list(range(pos, n_ph))
        for cs in range(0, len(non_rep), _REP_CHUNK_SIZE):
            groups.append(non_rep[cs:cs + _REP_CHUNK_SIZE])

    return [g for g in groups if g]


def _build_mini(seg: dict, real_ph_positions: list[int],
                stretch_factor: float = 1.0) -> tuple[dict, float]:
    """Build one mini DS entry from the given real-phoneme positions in seg.

    stretch_factor > 1.0 slows phoneme durations so DiffSinger synthesizes at
    a comfortable pace; the caller compresses the output audio back with atempo.
    """
    ph_seq = seg["ph_seq"].split()
    ph_dur = [float(d) for d in seg["ph_dur"].split()]
    f0_vals = [float(v) for v in seg.get("f0_seq", "").split()]
    f0_step = float(seg.get("f0_timestep", _F0_TIMESTEP))

    # real_ph_positions are indices into the real-phoneme list; map to ph_seq positions
    real_idx = [i for i, p in enumerate(ph_seq) if p not in ("AP", "SP")]
    chunk = [real_idx[rp] for rp in real_ph_positions]

    mini_phones = ["AP"] + [ph_seq[i] for i in chunk] + ["SP"]
    mini_durs   = [0.025] + [max(ph_dur[i] * stretch_factor, _MIN_PHONE_S) for i in chunk] + [0.025]
    mini_total  = sum(mini_durs)

    t_start = sum(ph_dur[:chunk[0]])
    t_end   = sum(ph_dur[:chunk[-1] + 1])
    f_s = max(0, int(t_start / f0_step))
    f_e = max(f_s + 1, min(len(f0_vals), int(t_end / f0_step)))

    orig_slice = f0_vals[f_s:f_e] if (f0_vals and f_s < len(f0_vals)) else []
    mini_frames = max(1, round(mini_total / f0_step))
    if orig_slice:
        ox = np.linspace(0, 1, len(orig_slice))
        nx = np.linspace(0, 1, mini_frames)
        mini_f0 = list(np.interp(nx, ox, orig_slice))
    else:
        mini_f0 = [220.0] * mini_frames

    mini = dict(seg)
    mini["ph_seq"]      = " ".join(mini_phones)
    mini["ph_dur"]      = " ".join(str(round(d, 4)) for d in mini_durs)
    mini["f0_seq"]      = " ".join(f"{v:.2f}" for v in mini_f0)
    mini["f0_timestep"] = str(f0_step)
    return mini, mini_total


def _split_to_mini_chunks(seg: dict, stretch_factor: float = 1.0) -> list[tuple[dict, float]]:
    """Split a REP segment into mini DS entries, splitting at exact repetition boundaries.

    E.g. [a ɾ e m o  n a i  n a i  n a i] →
         [a ɾ e m o], [n a i], [n a i], [n a i]
    Each [n a i] is synthesized in isolation — no repetition context, no attention ambiguity.
    stretch_factor slows each chunk so the model sings at a comfortable pace.
    Non-rep runs are kept together (capped at _REP_CHUNK_SIZE).
    """
    ph_seq = seg["ph_seq"].split()
    real_phones = [p for p in ph_seq if p not in ("AP", "SP")]
    groups = _rep_boundary_groups(real_phones)
    return [_build_mini(seg, g, stretch_factor) for g in groups if g]


def _mix(ds_output_wav: str, no_vocals_path: str, output_path: str,
         instr_offset_s: float = 0.0, vocal_gain: float = 1.4) -> None:
    print("  Mixing with instrumental...")
    vocal, v_sr = sf.read(ds_output_wav)
    instr, i_sr = sf.read(no_vocals_path)

    if vocal.ndim > 1:
        vocal = vocal.mean(axis=1)
    if instr.ndim > 1:
        instr = instr.mean(axis=1)

    if i_sr != v_sr:
        instr = _resample(instr.astype(np.float32), i_sr, v_sr)

    # Normalise vocal to a consistent RMS before applying the gain boost.
    # DiffSinger output amplitude varies run-to-run; normalising first makes
    # --vocal-gain behave predictably regardless of the raw synthesis level.
    vocal_rms = float(np.sqrt(np.mean(vocal ** 2)))
    if vocal_rms > 1e-6:
        vocal = vocal / vocal_rms * 0.08  # target RMS ~0.08 (typical sung vocal level)
    vocal = vocal * vocal_gain

    # Dynamic upward compression: boost quiet passages (soft/intimate sections) so
    # they sit audibly in the mix alongside the instrumental.
    # Uses a 200ms sliding RMS window; sections below the threshold are boosted
    # toward it at a 4:1 ratio (gentler than a limiter, avoids pumping artifacts).
    _comp_win = max(1, int(0.20 * v_sr))   # 200ms window
    _comp_hop = _comp_win // 4             # 75% overlap for smooth gain curve
    _threshold = 0.04                      # boost passages below this RMS
    _ratio     = 4.0
    _max_boost = 5.0                       # hard cap: never boost more than 5×
    gain_env = np.ones(len(vocal), dtype=np.float32)
    for _i in range(0, len(vocal), _comp_hop):
        _chunk = vocal[_i:_i + _comp_win]
        _rms = float(np.sqrt(np.mean(_chunk ** 2))) if len(_chunk) else 0.0
        if _rms > 1e-8 and _rms < _threshold:
            _g = min((_threshold / _rms) ** ((_ratio - 1.0) / _ratio), _max_boost)
            gain_env[_i:_i + _comp_win] = np.maximum(gain_env[_i:_i + _comp_win], _g)
    # Smooth gain envelope to prevent click artifacts at window boundaries
    from scipy.ndimage import uniform_filter1d as _uf1d
    gain_env = _uf1d(gain_env, size=_comp_win)
    vocal = (vocal * gain_env).astype(np.float32)

    # Correct the official instrumental's start position relative to the vocal track.
    if abs(instr_offset_s) > 0.005:
        offset_samples = int(round(abs(instr_offset_s) * v_sr))
        if instr_offset_s > 0:
            instr = np.concatenate([np.zeros(offset_samples, dtype=np.float32), instr])
        else:
            instr = instr[min(offset_samples, len(instr)):]

    # Pad shorter one
    n = max(len(vocal), len(instr))
    vocal = np.pad(vocal, (0, n - len(vocal)))
    instr = np.pad(instr, (0, n - len(instr)))

    mixed = instr * 0.8 + vocal
    peak = np.abs(mixed).max()
    if peak > 0.95:
        mixed = mixed / peak * 0.95

    sf.write(output_path, mixed.astype(np.float32), v_sr, subtype="PCM_16")
    print(f"  Output: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Sing any song in Koroki's voice with DiffSinger")
    parser.add_argument("query", help="Song search query (yt-dlp ytsearch)")
    parser.add_argument("--output", default=None)
    parser.add_argument("--whisper-model", default="medium",
                        choices=["tiny", "base", "small", "medium", "large"])
    parser.add_argument("--start", type=float, default=None,
                        help="Trim to this start time (seconds)")
    parser.add_argument("--duration", type=float, default=None,
                        help="Only process this many seconds")
    parser.add_argument("--no-search", action="store_true",
                        help="Skip YouTube search for official stems; always use demucs")
    parser.add_argument("--no-lyrics-search", action="store_true",
                        help="Skip online lyrics search; always use Whisper for transcription")
    parser.add_argument("--no-genius", action="store_true",
                        help="Skip Genius lyrics + CTC forced alignment; go straight to Whisper")
    parser.add_argument("--lyrics-file", default=None, metavar="FILE",
                        help="Plain text file with correct lyrics (one Japanese line per line). "
                             "Bypasses Genius and Whisper for text content. "
                             "Timing is derived from syncedlyrics partial data (if available) "
                             "or vocal RMS silence detection. Triggers per-line SOFA mode for "
                             "accurate within-segment phoneme timing.")
    parser.add_argument("--stems-only", action="store_true",
                        help="Stop after vocal/instrumental separation — listen to stems to diagnose separation quality")
    parser.add_argument("--save-stems", default=None, metavar="DIR",
                        help="Save separated stems here (default: data/debug_stems/<query>/)")
    parser.add_argument("--resume", action="store_true",
                        help="Legacy flag — now a no-op. The pipeline auto-detects stale outputs "
                             "and skips expensive stages (download, separation, SOFA) automatically.")
    parser.add_argument("--stop-after", default=None,
                        choices=["download", "separate", "transcribe", "align", "build", "infer"],
                        help="Stop after this stage and print the work dir so you can inspect the output.")
    parser.add_argument("--extract-training-data", action="store_true",
                        help="Extract aligned segments as DiffSinger training data instead of synthesizing")
    parser.add_argument("--training-dir", default=None, metavar="DIR",
                        help="Training data root (default: data/diffsinger_raw/japanese/)")
    parser.add_argument("--diffsinger-exp", default="koroki_v12", metavar="EXP",
                        help="DiffSinger experiment name (default koroki_v12: real-Ikura, variance embeds)")
    parser.add_argument("--diffsinger-ckpt", default=None, type=int, metavar="STEPS",
                        help="Specific checkpoint step to load (default: latest)")
    parser.add_argument("--no-rvc-chain", action="store_true",
                        help="Skip the RVC Korokiv5 post-pass (output raw DiffSinger/Ikura voice)")
    parser.add_argument("--no-variance", action="store_true",
                        help="Skip variance-curve extraction (for legacy models without variance embeds)")
    parser.add_argument("--diffsinger-spk", default=None, metavar="SPEAKER",
                        help="Speaker name for multi-speaker models (e.g. koroki, yoasobi). "
                             "Supports blending: 'koroki:0.7|yoasobi:0.3' mixes speaker embeddings. "
                             "Ignored by single-speaker models.")
    parser.add_argument("--vocal-gain", default=1.4, type=float, metavar="GAIN",
                        help="Vocal volume multiplier after RMS normalisation (default: 1.4). "
                             "Increase if voice is too quiet, decrease if it clips.")
    parser.add_argument("--key-shift", default=0, type=int, metavar="SEMITONES",
                        help="Transpose the song by N semitones (negative = lower key). "
                             "Shifts both the vocal synthesis and the instrumental so they stay in sync. "
                             "Use to bring high songs into Koroki's comfortable range, e.g. --key-shift -3")
    parser.add_argument("--use-sofa", action="store_true",
                        help="Force SOFA aligner instead of note-based AMT (for debugging/comparison)")
    parser.add_argument("--use-basic-pitch", action="store_true",
                        help="Force Basic Pitch AMT instead of CREPE for note detection")
    parser.add_argument("--use-htdemucs", action="store_true",
                        help="Force htdemucs_ft separator instead of BS-Roformer (better on quiet endings)")
    args = parser.parse_args()

    if not _DIFFSINGER_DIR.exists():
        sys.exit(f"DiffSinger not found at {_DIFFSINGER_DIR}")

    output_path = args.output or str(
        _REPO_ROOT / "data" / "diffsinger_output" / f"{args.query[:40].replace(' ','_')}.wav"
    )
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Persistent per-song work directory — never deleted, survives across runs.
    # Each stage saves a canonical output file here. Use --resume to skip stages
    # whose file already exists. Delete a file to force that stage to re-run.
    slug = re.sub(r"[^\w\-]", "_", args.query)[:50].strip("_")
    work_dir = _REPO_ROOT / "data" / "diffsinger_work" / slug
    work_dir.mkdir(parents=True, exist_ok=True)

    source_p     = work_dir / "source.wav"
    vocals_p     = work_dir / "vocals.wav"
    instr_p      = work_dir / "instrumental.wav"
    transcript_p = work_dir / "transcript.json"
    alignment_p  = work_dir / "alignment.textgrid"
    segments_p   = work_dir / "segments.ds"
    synth_p      = work_dir / "synth.wav"
    state_p      = work_dir / "state.json"

    def _can_skip(path: Path) -> bool:
        return path.exists() and path.stat().st_size > 0

    def _stop_here(stage: str) -> None:
        if args.stop_after == stage:
            print(f"\n  Stopped after '{stage}'.")
            print(f"  Work dir: {work_dir}")
            for f in sorted(work_dir.iterdir()):
                kb = f.stat().st_size // 1024
                print(f"    {f.name:<30} {kb:>6} KB")
            sys.exit(0)

    # ── Stage 1: Download ────────────────────────────────────────────────────
    if _can_skip(source_p):
        print(f"  [resume] download  → {source_p.name}")
        source = str(source_p)
    else:
        source = _download(args.query, str(work_dir))
        if Path(source) != source_p:
            shutil.copy2(source, source_p)
        source = str(source_p)
    _stop_here("download")

    # ── Stage 2: Separate ────────────────────────────────────────────────────
    instr_offset = 0.0
    if _can_skip(vocals_p) and _can_skip(instr_p):
        print(f"  [resume] separate  → vocals.wav + instrumental.wav")
        vocals_path    = str(vocals_p)
        no_vocals_path = str(instr_p)
        state = json.loads(state_p.read_text()) if state_p.exists() else {}
        instr_offset = float(state.get("instr_offset", 0.0))
    else:
        off_instr = None if args.no_search else _search_official_stems(args.query, str(work_dir))
        if args.use_htdemucs:
            print("  Separating vocals (htdemucs_ft, forced)...")
            raw_vocals, demucs_instr = _separate_htdemucs(source, str(work_dir))
        else:
            raw_vocals, demucs_instr = _separate(source, str(work_dir))
        no_vocals_raw = off_instr or demucs_instr

        if off_instr:
            print("  Using official instrumental for final mix")
            instr_offset = _find_instr_offset(demucs_instr, off_instr)
            if abs(instr_offset) > 0.005:
                print(f"  Instrumental offset: {instr_offset:+.3f}s — correcting in mix")

        shutil.copy2(raw_vocals, vocals_p)
        shutil.copy2(no_vocals_raw, instr_p)
        state_p.write_text(json.dumps({"instr_offset": instr_offset,
                                        "official_instr": off_instr is not None}))
        vocals_path    = str(vocals_p)
        no_vocals_path = str(instr_p)

    stems_debug_dir = Path(args.save_stems) if args.save_stems else (
        _REPO_ROOT / "data" / "debug_stems" / args.query[:40].replace(" ", "_")
    )
    stems_debug_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(vocals_path, str(stems_debug_dir / "vocals.wav"))
    shutil.copy2(no_vocals_path, str(stems_debug_dir / "instrumental.wav"))
    print(f"\n  Stems saved → {work_dir}")
    print(f"    vocals.wav       ← listen to check separation quality")
    print(f"    instrumental.wav ← what Koroki will sing over")

    if args.stems_only:
        print("\n  --stems-only: stopping here.")
        sys.exit(0)
    _stop_here("separate")

    # Optional trim (applied in-place; forces re-run of downstream stages if used with --resume)
    if args.start is not None or args.duration is not None:
        audio, sr = sf.read(vocals_path)
        instr, _  = sf.read(no_vocals_path)
        s = int((args.start or 0) * sr)
        e = int(s + args.duration * sr) if args.duration else len(audio)
        audio = audio[s:e] if audio.ndim == 1 else audio[s:e, :]
        instr = instr[s:e] if instr.ndim == 1 else instr[s:e, :]
        sf.write(vocals_path, audio, sr, subtype="PCM_16")
        sf.write(no_vocals_path, instr, sr, subtype="PCM_16")
        print(f"  Trimmed to {(e-s)/sr:.1f}s starting at {args.start or 0}s")

    # ── Stage 3: Transcribe / Lyrics fetch ──────────────────────────────────
    # Auto-invalidation: if the lyrics library file is newer than transcript.json,
    # delete the cached transcript so the pipeline re-fetches with the updated lyrics.
    _lyrics_lib_dir_pre = _REPO_ROOT / "data" / "diffsinger_lyrics"
    _auto_lyrics_pre = _lyrics_lib_dir_pre / f"{slug}.txt"
    if transcript_p.exists() and _auto_lyrics_pre.exists():
        if _auto_lyrics_pre.stat().st_mtime > transcript_p.stat().st_mtime:
            print("  Lyrics file updated — rebuilding transcript")
            transcript_p.unlink()

    if _can_skip(transcript_p):
        print(f"  [resume] transcribe → transcript.json")
        td = json.loads(transcript_p.read_text(encoding="utf-8"))
        transcript   = td["text"]
        whisper_segs = td["segments"]
        src = td.get("source", "unknown")
        print(f"  {len(whisper_segs)} segments (source: {src})")
    else:
        transcript, whisper_segs, src = None, None, None

        # 1. Try online synced lyrics first — accurate timestamps, no hallucinations
        if not args.no_lyrics_search:
            result = _fetch_synced_lyrics(args.query)
            if result:
                transcript, whisper_segs = result
                # Coverage check: if synced lyrics only cover a fraction of the
                # song, the LRC source is likely incomplete (missing repeated
                # chorus sections).
                audio_duration = sf.info(vocals_path).duration
                lyric_end = whisper_segs[-1]["end"] if whisper_segs else 0
                coverage = lyric_end / audio_duration if audio_duration > 0 else 1.0
                if coverage < 0.70:
                    print(f"  Synced lyrics only cover {coverage*100:.0f}% of song "
                          f"({lyric_end:.0f}s of {audio_duration:.0f}s) — "
                          f"missing repeated sections. Fetching full lyrics from Genius.")
                    transcript, whisper_segs = None, None
                else:
                    src = "syncedlyrics"
                    # Duration mismatch check: warn if LRC lines extend past actual vocal content.
                    # Catches cases where LRC is from a longer version than the downloaded audio.
                    src_audio, src_sr = sf.read(source)
                    if src_audio.ndim > 1:
                        src_audio = src_audio.mean(axis=1)
                    eff_end = _effective_audio_end(src_audio.astype(np.float32), src_sr)
                    overrun = [s for s in whisper_segs if float(s["start"]) > eff_end + 2.0]
                    if overrun:
                        print(f"  WARN: {len(overrun)} lyric line(s) start past effective audio end "
                              f"({eff_end:.1f}s) — will be skipped (no vocal signal):")
                        for s in overrun:
                            print(f"    {s['start']:.1f}s  {s.get('text','').strip()}")

        # 1.5. User-provided lyrics file (correct text, bypasses Genius and Whisper).
        # Checks automatically: data/diffsinger_lyrics/<query_slug>.txt
        # Can also be forced with --lyrics-file. Timing derived from:
        # syncedlyrics partial timestamps (if >= 20% coverage) → RMS silence fallback.
        # Triggers per-line SOFA: correct phonemes aligned per window → accurate timing.
        _lyrics_lib_dir = _REPO_ROOT / "data" / "diffsinger_lyrics"
        _auto_lyrics = _lyrics_lib_dir / f"{slug}.txt"
        _lyrics_file_path = Path(args.lyrics_file) if args.lyrics_file else (
            _auto_lyrics if _auto_lyrics.exists() else None
        )
        if transcript is None and _lyrics_file_path and _lyrics_file_path.exists():
            args.lyrics_file = str(_lyrics_file_path)  # normalise for downstream use
        if transcript is None and args.lyrics_file:
            lyrics_path = Path(args.lyrics_file)
            if not lyrics_path.exists():
                print(f"  WARN: --lyrics-file '{args.lyrics_file}' not found — skipping")
            else:
                user_lines = [
                    _normalize_transcript(l.strip())
                    for l in lyrics_path.read_text(encoding="utf-8").splitlines()
                    if l.strip()
                ]
                if user_lines:
                    print(f"  Lyrics: {len(user_lines)} lines from {lyrics_path.name}")
                    sys.path.insert(0, str(_SELF_DIR))
                    from lyrics_align import rms_silence_align  # type: ignore

                    # If syncedlyrics gave us partial timing (thrown away due to < 70%
                    # coverage), re-fetch it here as a timing anchor for the early lines.
                    partial_segs: list[dict] | None = None
                    try:
                        partial_result = _fetch_synced_lyrics(args.query)
                        if partial_result:
                            _, partial_segs = partial_result
                    except Exception:
                        pass

                    if partial_segs and len(partial_segs) >= 5:
                        # Use syncedlyrics timestamps for the first N user lines,
                        # then RMS silence alignment on the remaining vocal tail.
                        # RMS finds actual silence gaps in the audio rather than
                        # distributing uniformly — avoids gaps where windows fall
                        # in instrumental sections.
                        anchor_n = min(len(partial_segs), len(user_lines))
                        segs = [
                            {"start": ps["start"], "end": ps["end"], "text": user_lines[i]}
                            for i, ps in enumerate(partial_segs[:anchor_n])
                        ]
                        if len(user_lines) > anchor_n:
                            remaining = user_lines[anchor_n:]
                            rem_start = partial_segs[anchor_n - 1]["end"]
                            tail_segs = rms_silence_align(
                                vocals_path, remaining, audio_start_s=rem_start
                            )
                            segs.extend(tail_segs)
                        synced_n = len(partial_segs)
                        tail_n = len(user_lines) - anchor_n
                        print(f"  Timing: {synced_n} lines from syncedlyrics, "
                              f"{tail_n} from RMS silence detection")
                    else:
                        segs = rms_silence_align(vocals_path, user_lines)

                    whisper_segs = segs
                    transcript = " ".join(s["text"] for s in segs)
                    src = "syncedlyrics"  # triggers per-line SOFA mode

        # 1.6. Automatic lyrics fetch: uta-net → Genius (Japanese text + RMS timing).
        # uta-net.com is Japan's largest lyrics site and always returns Japanese text
        # (no English translation risk). Genius is tried as fallback with retries.
        # Both feed into rms_silence_align for timing, then per-line SOFA handles
        # per-phoneme alignment within each window.
        if transcript is None and not args.no_lyrics_search and not args.no_genius:
            sys.path.insert(0, str(_SELF_DIR))
            try:
                from lyrics_align import (  # type: ignore
                    fetch_genius_lyrics, fetch_utanet_lyrics, rms_silence_align,
                )
                auto_lines = fetch_utanet_lyrics(args.query)
                source_tag = "uta-net"
                if not auto_lines:
                    auto_lines = fetch_genius_lyrics(args.query)
                    source_tag = "genius"
                if auto_lines:
                    # Sync the first portion against syncedlyrics timestamps if available
                    partial_segs_auto: list[dict] | None = None
                    try:
                        _pr = _fetch_synced_lyrics(args.query)
                        if _pr:
                            _, partial_segs_auto = _pr
                    except Exception:
                        pass
                    if partial_segs_auto and len(partial_segs_auto) >= 5:
                        anchor_n = min(len(partial_segs_auto), len(auto_lines))
                        segs = [
                            {"start": ps["start"], "end": ps["end"], "text": auto_lines[i]}
                            for i, ps in enumerate(partial_segs_auto[:anchor_n])
                        ]
                        if len(auto_lines) > anchor_n:
                            rem_start = partial_segs_auto[anchor_n - 1]["end"]
                            tail_segs = rms_silence_align(
                                vocals_path, auto_lines[anchor_n:], audio_start_s=rem_start
                            )
                            segs.extend(tail_segs)
                        print(f"  [{source_tag}] timing: {anchor_n} syncedlyrics + "
                              f"{len(auto_lines)-anchor_n} RMS")
                    else:
                        segs = rms_silence_align(vocals_path, auto_lines)
                    whisper_segs = segs
                    transcript = _normalize_transcript(
                        " ".join(s["text"] for s in segs)
                    )
                    src = "syncedlyrics"  # triggers per-line SOFA mode
            except Exception as e:
                print(f"  [auto-lyrics] failed: {e}")

        # 2. Fall back to Whisper transcription (last resort)
        if transcript is None:
            try:
                import whisper as _whisper
            except ImportError:
                sys.exit("Run: .venv_diffsinger\\Scripts\\pip install openai-whisper")
            print(f"  Loading Whisper ({args.whisper_model})...")
            whisper_model = _whisper.load_model(args.whisper_model)
            transcript, whisper_segs = _transcribe(vocals_path, whisper_model)
            src = "whisper"

        print(f"  {len(whisper_segs)} segments (source: {src})")
        if not transcript.strip():
            sys.exit("No lyrics — try without --no-lyrics-search or provide a different query.")
        transcript_p.write_text(
            json.dumps({"text": transcript, "segments": whisper_segs, "source": src},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    _stop_here("transcribe")

    # ── Stage 4: Align ───────────────────────────────────────────────────────
    # Alignment priority (highest to lowest):
    #   1. Basic Pitch AMT  — note detection from audio → mora mapping → IPA intervals
    #                          (active when transcript has per-line timestamps AND
    #                           basic-pitch is installed AND --use-sofa is NOT passed)
    #   2. SOFA per-line    — forced alignment per lyric clip (fallback if AMT fails)
    #   3. SOFA full-track  — whole-song SOFA (fallback if per-line SOFA fails)
    #   4. MFA              — last resort (speech aligner, poor on singing)
    #
    # alignment_segs.json is shared by both AMT and per-line SOFA — same format.
    alignment_segs_p = work_dir / "alignment_segs.json"

    td_meta = json.loads(transcript_p.read_text(encoding="utf-8"))
    transcript_source = td_meta.get("source", "whisper")
    _has_line_timestamps = transcript_source in ("syncedlyrics", "genius_ctc", "genius_paced")
    # use_segmented: controls whether _build_ds_segments gets lyric boundaries and skips
    # RMS vowel splitting — True for both AMT and per-line SOFA paths.
    use_segmented = _has_line_timestamps and (
        _sofa_available() or _whisperx_available() or _crepe_available() or _basic_pitch_available()
    )

    # Alignment priority (highest to lowest):
    #   1. Wav2Vec2 CTC (whisperx) — audio-grounded, robust on rapid sections
    #   2. CREPE / Basic Pitch AMT — note-counting fallback
    #   3. SOFA per-line           — forced aligner fallback
    # --use-sofa: skip 1+2, go straight to SOFA.
    # --use-basic-pitch: skip 1, use Basic Pitch AMT (not CREPE or Wav2Vec2).
    _use_wav2vec2 = (
        _has_line_timestamps and _whisperx_available()
        and not args.use_sofa and not args.use_basic_pitch
    )
    _use_amt = (
        _has_line_timestamps and (_crepe_available() or _basic_pitch_available())
        and not args.use_sofa
    )

    # Invalidate cached alignment if transcript is newer (stale alignment = 0 segments)
    if transcript_p.exists():
        t_mtime = transcript_p.stat().st_mtime
        if alignment_segs_p.exists() and t_mtime > alignment_segs_p.stat().st_mtime:
            print("  alignment_segs.json outdated (transcript changed) — re-aligning")
            alignment_segs_p.unlink()
        if alignment_p.exists() and t_mtime > alignment_p.stat().st_mtime:
            print("  alignment.textgrid outdated (transcript changed) — re-aligning")
            alignment_p.unlink()

    intervals: list[tuple] = []

    if use_segmented and _can_skip(alignment_segs_p):
        aligner_tag = "AMT" if _use_amt else "per-line SOFA"
        print(f"  [resume] align     → alignment_segs.json ({aligner_tag})")
        intervals = [tuple(iv) for iv in json.loads(alignment_segs_p.read_text(encoding="utf-8"))]
    elif not use_segmented and _can_skip(alignment_p):
        print(f"  [resume] align     → alignment.textgrid")
        intervals = _parse_textgrid_sofa(str(alignment_p))
        intervals = _apply_whisper_boundaries(intervals, whisper_segs)
    else:
        # ── 4a. Wav2Vec2 CTC forced alignment (primary — audio-grounded) ────
        if _use_wav2vec2:
            try:
                intervals = _run_wav2vec2_align(vocals_path, whisper_segs, str(work_dir))
                alignment_segs_p.write_text(
                    json.dumps(intervals, ensure_ascii=False), encoding="utf-8"
                )
                print(f"  Wav2Vec2 alignment saved → alignment_segs.json")
            except Exception as e:
                print(f"  Wav2Vec2 failed ({e}), falling back to AMT...")
                intervals = []

        # ── 4b. Note detection AMT (CREPE primary → Basic Pitch fallback) ───
        if not intervals and _use_amt:
            try:
                use_bp = args.use_basic_pitch or not _crepe_available()
                if use_bp:
                    midi_notes = _run_basic_pitch(vocals_path, str(work_dir))
                else:
                    midi_notes = _run_crepe(vocals_path, str(work_dir))
                intervals = _align_amt_notes_to_lyrics(midi_notes, whisper_segs)
                alignment_segs_p.write_text(
                    json.dumps(intervals, ensure_ascii=False), encoding="utf-8"
                )
                print(f"  AMT alignment saved → alignment_segs.json")
            except Exception as e:
                print(f"  AMT failed ({e}), falling back to SOFA per-line...")
                intervals = []

        # ── 4b. SOFA per-line (fallback when AMT failed / --use-sofa) ───────
        if not intervals and use_segmented:
            try:
                intervals = _run_sofa_segmented(vocals_path, whisper_segs, str(work_dir))
                alignment_segs_p.write_text(
                    json.dumps(intervals, ensure_ascii=False), encoding="utf-8"
                )
            except Exception as e:
                print(f"  Per-line SOFA failed ({e}), falling back to full-track mode...")
                use_segmented = False

        # ── 4c. SOFA full-track ──────────────────────────────────────────────
        if not intervals:
            if _sofa_available():
                try:
                    tg_path = _run_sofa(vocals_path, transcript, str(work_dir), segments=whisper_segs)
                    intervals = _parse_textgrid_sofa(tg_path)
                    intervals = _apply_whisper_boundaries(intervals, whisper_segs)
                    shutil.copy2(tg_path, alignment_p)
                except Exception as e:
                    print(f"  SOFA failed ({e}), falling back to MFA...")

        # ── 4d. MFA (last resort) ────────────────────────────────────────────
        if not intervals:
            if not _sofa_available():
                print("  SOFA checkpoint not found — using MFA")
            tg_path = _run_mfa(vocals_path, transcript, str(work_dir))
            intervals = _parse_textgrid(tg_path)
            shutil.copy2(tg_path, alignment_p)

        if not intervals:
            sys.exit("Alignment produced no intervals.")
    _stop_here("align")

    # ── Stage 5: Build segments ──────────────────────────────────────────────
    # Auto-invalidation: rebuild segments.ds if transcript, alignment, or this script changed.
    if segments_p.exists():
        segs_mtime = segments_p.stat().st_mtime
        script_mtime = Path(__file__).stat().st_mtime
        stale = (
            (transcript_p.exists() and transcript_p.stat().st_mtime > segs_mtime)
            or (alignment_segs_p.exists() and alignment_segs_p.stat().st_mtime > segs_mtime)
            or (alignment_p.exists() and alignment_p.stat().st_mtime > segs_mtime)
            or script_mtime > segs_mtime
        )
        if stale:
            print("  segments.ds outdated — rebuilding")
            segments_p.unlink()

    if _can_skip(segments_p):
        print(f"  [resume] build     → segments.ds")
        segments = json.loads(segments_p.read_text(encoding="utf-8"))
        print(f"  {len(segments)} segments")
        vocals_audio, v_sr = sf.read(vocals_path)
        if vocals_audio.ndim > 1:
            vocals_audio = vocals_audio.mean(axis=1)
        vocals_audio = vocals_audio.astype(np.float32)
    else:
        vocals_audio, v_sr = sf.read(vocals_path)
        if vocals_audio.ndim > 1:
            vocals_audio = vocals_audio.mean(axis=1)
        vocals_audio = vocals_audio.astype(np.float32)
        # RMS vowel splitting is only needed in Whisper mode to fix phrase-final vowels
        # extending into inter-phrase gaps. In per-line SOFA mode each lyric line already
        # has its own bounded clip so phrase gaps are guaranteed — splitting here would
        # incorrectly cut held notes at energy dips and leave multi-second silence gaps.
        if not use_segmented:
            intervals = _split_long_vowels_by_rms(intervals, vocals_audio, v_sr)
        print("  Building DS segments + extracting F0...")
        lyric_bounds = [seg["start"] for seg in whisper_segs] if use_segmented else None
        segments = _build_ds_segments(intervals, vocals_audio, v_sr, lyric_boundaries=lyric_bounds)
        print(f"  {len(segments)} segments")
        if not segments:
            sys.exit("No valid segments produced — transcript may not have matched audio.")
        segments_p.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
    _stop_here("build")

    # ── Training data extraction mode (exits here) ───────────────────────────
    if args.extract_training_data:
        import csv as _csv

        train_dir = Path(args.training_dir) if args.training_dir else (
            _REPO_ROOT / "data" / "diffsinger_raw" / "japanese"
        )
        wavs_dir = train_dir / "wavs"
        wavs_dir.mkdir(parents=True, exist_ok=True)
        csv_path = train_dir / "transcriptions.csv"

        existing_names: set[str] = set()
        if csv_path.exists():
            with csv_path.open(encoding="utf-8") as f:
                existing_names = {row["name"] for row in _csv.DictReader(f)}

        train_slug = re.sub(r"[^\w]+", "_", args.query[:30]).strip("_").lower()
        _MIN_TRAIN_PHONES = 5
        _MIN_TRAIN_DUR = 1.5

        written, skipped = 0, 0
        new_rows = []
        for i, seg in enumerate(segments):
            ph_list = seg["ph_seq"].split()
            real_ph = [p for p in ph_list if p not in ("AP", "SP")]
            dur_vals = [float(d) for d in seg["ph_dur"].split()]
            total_dur = sum(dur_vals)

            if len(real_ph) < _MIN_TRAIN_PHONES or total_dur < _MIN_TRAIN_DUR:
                skipped += 1
                continue

            item_id = f"{train_slug}_seg{i:04d}"
            if item_id in existing_names:
                print(f"  SKIP {item_id} (already in training set)")
                skipped += 1
                continue

            offset = seg["offset"]
            s = int(offset * v_sr)
            e = int((offset + total_dur) * v_sr)
            clip = vocals_audio[s:min(e, len(vocals_audio))]

            actual_dur = len(clip) / v_sr
            if abs(actual_dur - total_dur) > 0.005 and total_dur > 0:
                scale = actual_dur / total_dur
                dur_vals = [round(d * scale, 4) for d in dur_vals]

            wav_out = wavs_dir / f"{item_id}.wav"
            sf.write(str(wav_out), clip, v_sr, subtype="PCM_16")
            new_rows.append({
                "name": item_id,
                "ph_seq": seg["ph_seq"],
                "ph_dur": " ".join(str(d) for d in dur_vals),
            })
            existing_names.add(item_id)
            written += 1

        write_header = not csv_path.exists() or csv_path.stat().st_size == 0
        with csv_path.open("a", newline="", encoding="utf-8") as f:
            writer = _csv.DictWriter(f, fieldnames=["name", "ph_seq", "ph_dur"])
            if write_header:
                writer.writeheader()
            writer.writerows(new_rows)

        print(f"\n  Extracted {written} training segments → {train_dir}")
        if skipped:
            print(f"  Skipped {skipped} (too short or already present)")
        print(f"  Re-binarize with: .venv_diffsinger\\Scripts\\python DiffSinger\\scripts\\binarize.py --config <your_config.yaml>")
        return

    # ── Auto key shift ───────────────────────────────────────────────────────
    effective_key_shift = args.key_shift
    if effective_key_shift == 0:
        effective_key_shift, p90_hz = _compute_auto_key_shift(segments)
        if effective_key_shift != 0:
            print(f"  Auto-transposing {effective_key_shift:+d} semitones "
                  f"(song p90 F0: {p90_hz:.0f} Hz, Koroki ceiling: {_F0_COMFORTABLE_MAX:.0f} Hz)")
        else:
            print(f"  Key shift: none (song p90 F0: {p90_hz:.0f} Hz, ceiling: {_F0_COMFORTABLE_MAX:.0f} Hz)")

    # ── Stage 6: DiffSinger inference ────────────────────────────────────────
    # Auto-invalidation: re-synthesize if segments.ds changed since last synth.
    if synth_p.exists() and segments_p.exists():
        if segments_p.stat().st_mtime > synth_p.stat().st_mtime:
            print("  segments.ds updated — re-synthesizing")
            synth_p.unlink()

    if _can_skip(synth_p):
        print(f"  [resume] infer     → synth.wav")
        ds_out_wav = str(synth_p)
    else:
        if not args.no_variance:
            print("  Extracting variance curves from source vocal (energy/breathiness/voicing)...")
            _add_source_variances(segments, vocals_path)
        ds_path = str(work_dir / "song.ds")
        Path(ds_path).write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
        print("  Running DiffSinger inference...")
        ds_out_dir = str(work_dir / "ds_out")
        os.makedirs(ds_out_dir, exist_ok=True)
        sys.path.insert(0, str(_DIFFSINGER_DIR))
        orig_dir = os.getcwd()
        os.chdir(str(_DIFFSINGER_DIR))
        try:
            infer_cmd = [sys.executable, "scripts/infer.py", "acoustic", ds_path,
                         "--exp", args.diffsinger_exp, "--out", ds_out_dir]
            if args.diffsinger_ckpt is not None:
                infer_cmd += ["--ckpt", str(args.diffsinger_ckpt)]
            if args.diffsinger_spk is not None:
                infer_cmd += ["--spk", args.diffsinger_spk]
            result = subprocess.run(infer_cmd, capture_output=False, timeout=300)
            if result.returncode != 0:
                raise RuntimeError("DiffSinger inference failed")
            wavs = sorted(Path(ds_out_dir).rglob("*.wav"))
            if not wavs:
                raise RuntimeError("DiffSinger produced no output WAV")
            ds_out_wav_raw = str(wavs[0])
        finally:
            os.chdir(orig_dir)
        shutil.copy2(ds_out_wav_raw, synth_p)
        ds_out_wav = str(synth_p)

        # ── Rap time-stretch pass ─────────────────────────────────────────────
        # Segments where avg phone duration < 90ms cause dropout/voice loss.
        # Empirically measured: 87-88ms segments drop out at 29-40s, 69ms drops
        # out at end of 89-phone segment at 93s. Floor is ~90ms.
        # Repetitive n-gram patterns (nai-nai-nai etc.) need an even higher target —
        # DiffSinger attention gets ambiguous when the same phoneme group repeats
        # consecutively, producing low-amplitude output even at 110ms avg.
        # Fix: re-synthesize those segments at a comfortable pace, then time-compress
        # the output back to original duration using pitch-preserving time stretch.
        _RAP_THRESHOLD_MS     = 90.0   # general fast-phoneme floor
        _RAP_THRESHOLD_REP_MS = 100.0  # repetitive-pattern floor (more lenient trigger)
        _RAP_TARGET_MS        = 95.0   # just above confidence floor — minimizes atempo ratio
        _RAP_TARGET_MS_REP    = 110.0  # isolated mini-chunks + correct f0 = confident at 110ms
        _RAP_MIN_PHONES       = 5

        def _is_rep_seg(s: dict) -> bool:
            real = [p for p in s["ph_seq"].split() if p not in ("AP", "SP")]
            return _has_repetitive_pattern(real)

        rap_indices = [
            i for i, s in enumerate(segments)
            if len([p for p in s["ph_seq"].split() if p not in ("AP", "SP")]) >= _RAP_MIN_PHONES
            and (
                s.get("avg_phone_ms", 100) < _RAP_THRESHOLD_MS
                or (s.get("avg_phone_ms", 100) < _RAP_THRESHOLD_REP_MS and _is_rep_seg(s))
            )
        ]

        if rap_indices:
            n_rep  = sum(1 for i in rap_indices if _is_rep_seg(segments[i]))
            n_fast = len(rap_indices) - n_rep
            print(f"  Rap pass: {n_fast} fast segment(s) [stretch], "
                  f"{n_rep} repetitive segment(s) [chunk-synthesis] → re-synthesizing...")

            # rap_ds_entries: flat list of DS dicts that go into song_rap.ds
            # rap_sources: one entry per rap_index describing how to reassemble its audio
            rap_ds_entries: list[dict] = []
            rap_sources: list[dict]    = []   # {is_rep, orig_offset, orig_dur, entry_slice, factor}

            seq_off = 0.0
            for i in rap_indices:
                seg       = segments[i]
                avg_ms    = seg["avg_phone_ms"]
                is_rep    = _is_rep_seg(seg)
                orig_dur  = sum(float(d) for d in seg["ph_dur"].split())
                entry_start = len(rap_ds_entries)

                if is_rep:
                    # Split into mini-chunks — each synthesized without repetition context,
                    # and with durations stretched so the model sings at a comfortable pace.
                    rep_factor = min(3.0, _RAP_TARGET_MS_REP / avg_ms)
                    minis = _split_to_mini_chunks(seg, rep_factor)
                    for mini_dict, mini_dur in minis:
                        mini_dict = dict(mini_dict)
                        mini_dict["offset"] = round(seq_off, 6)
                        rap_ds_entries.append(mini_dict)
                        seq_off += mini_dur + 0.02  # small gap between minis
                    rap_sources.append({
                        "is_rep":       True,
                        "orig_offset":  float(seg["offset"]),
                        "orig_dur":     orig_dur,
                        "entry_slice":  (entry_start, len(rap_ds_entries)),
                        "mini_durs":    [m[1] for m in minis],
                        "factor":       None,  # computed after concatenation
                    })
                else:
                    nominal_factor = min(3.0, _RAP_TARGET_MS / avg_ms)
                    stretched, actual_factor = _stretch_rap_segment(seg, nominal_factor)
                    stretched["offset"] = round(seq_off, 6)
                    stretched_dur = sum(float(d) for d in stretched["ph_dur"].split())
                    rap_ds_entries.append(stretched)
                    rap_sources.append({
                        "is_rep":       False,
                        "orig_offset":  float(seg["offset"]),
                        "orig_dur":     orig_dur,
                        "entry_slice":  (entry_start, entry_start + 1),
                        "mini_durs":    [stretched_dur],
                        "factor":       actual_factor,
                    })
                    seq_off += stretched_dur + 0.05

            if not args.no_variance:
                _add_flat_variances(rap_ds_entries)
            rap_ds_path = str(work_dir / "song_rap.ds")
            Path(rap_ds_path).write_text(
                json.dumps(rap_ds_entries, ensure_ascii=False, indent=2), encoding="utf-8")

            rap_out_dir = str(work_dir / "ds_out_rap")
            os.makedirs(rap_out_dir, exist_ok=True)
            os.chdir(str(_DIFFSINGER_DIR))
            try:
                rap_cmd = [sys.executable, "scripts/infer.py", "acoustic", rap_ds_path,
                           "--exp", args.diffsinger_exp, "--out", rap_out_dir]
                if args.diffsinger_ckpt is not None:
                    rap_cmd += ["--ckpt", str(args.diffsinger_ckpt)]
                if args.diffsinger_spk is not None:
                    rap_cmd += ["--spk", args.diffsinger_spk]
                result = subprocess.run(rap_cmd, capture_output=False, timeout=300)
                if result.returncode != 0:
                    raise RuntimeError("DiffSinger rap inference failed")
                rap_wavs = sorted(Path(rap_out_dir).rglob("*.wav"))
                if not rap_wavs:
                    raise RuntimeError("DiffSinger rap pass produced no output WAV")
                rap_out_wav = str(rap_wavs[0])
            finally:
                os.chdir(orig_dir)

            rap_audio, rap_sr = sf.read(rap_out_wav)
            if rap_audio.ndim > 1:
                rap_audio = rap_audio.mean(axis=1)
            rap_audio = rap_audio.astype(np.float32)

            normal_audio, n_sr = sf.read(ds_out_wav)
            if normal_audio.ndim > 1:
                normal_audio = normal_audio.mean(axis=1)
            normal_audio = normal_audio.astype(np.float32)

            _xfade_s = int(0.010 * rap_sr)  # 10ms crossfade between mini-chunks

            # Reconstruct offset→seq_off mapping from entry offsets in rap_ds_entries
            entry_offsets = [float(e["offset"]) for e in rap_ds_entries]

            for src in rap_sources:
                es, ee = src["entry_slice"]
                mini_durs = src["mini_durs"]
                orig_offset = src["orig_offset"]
                orig_dur    = src["orig_dur"]

                if src["is_rep"]:
                    # Concatenate mini-chunk audio, trimming the AP/SP silence padding
                    # from internal chunks so repetitions flow directly into each other.
                    # DiffSinger still synthesizes with AP/SP for clean onset/decay, but
                    # leaving 50ms of silence between each "nai" repetition caused the
                    # model's onset burst at each AP→consonant transition to sound like
                    # a brief flash of extra content. Trimming collapses the gaps.
                    _mini_ap_s = int(0.025 * rap_sr)  # AP duration hardcoded in _build_mini
                    _mini_sp_s = int(0.025 * rap_sr)  # SP duration hardcoded in _build_mini
                    n_minis = ee - es
                    chunks: list[np.ndarray] = []
                    for k, (entry_idx, mini_dur) in enumerate(zip(range(es, ee), mini_durs)):
                        is_first_mini = (k == 0)
                        is_last_mini  = (k == n_minis - 1)
                        e_off = entry_offsets[entry_idx]
                        e_s = int(e_off * rap_sr)
                        e_e = int((e_off + mini_dur) * rap_sr)
                        chunk = rap_audio[e_s:min(e_e, len(rap_audio))].copy()
                        if len(chunk) < 10:
                            continue
                        # Trim AP from non-first chunks, SP from non-last chunks
                        trim_head = 0 if is_first_mini else _mini_ap_s
                        trim_tail = 0 if is_last_mini  else _mini_sp_s
                        chunk = chunk[trim_head : max(trim_head + 10, len(chunk) - trim_tail)]
                        if len(chunk) < 10:
                            continue
                        # 5ms crossfade at join between trimmed chunks
                        _join_xf = int(0.005 * rap_sr)
                        if chunks and len(chunks[-1]) >= _join_xf and len(chunk) >= _join_xf:
                            ramp = np.linspace(1.0, 0.0, _join_xf, dtype=np.float32)
                            chunks[-1][-_join_xf:] *= ramp
                            chunk[:_join_xf] *= (1.0 - ramp)
                        chunks.append(chunk)

                    if not chunks:
                        continue
                    concat = np.concatenate(chunks)
                    concat_dur = len(concat) / rap_sr
                    factor = concat_dur / orig_dur if orig_dur > 0 else 1.0
                    compressed = _atempo_compress(concat, rap_sr, factor, work_dir)
                else:
                    e_off = entry_offsets[es]
                    stretched_dur = mini_durs[0]
                    r_s = int(e_off * rap_sr)
                    r_e = int((e_off + stretched_dur) * rap_sr)
                    seg_audio = rap_audio[r_s:min(r_e, len(rap_audio))]
                    if len(seg_audio) < 100:
                        continue
                    compressed = _atempo_compress(seg_audio, rap_sr, float(src["factor"]), work_dir)

                o_s = int(orig_offset * n_sr)
                # Zero out the original segment range before placing compressed audio.
                # The normal pass already synthesized this region (poorly — that's why
                # it's being re-synthesized), and blending FROM that wrong content
                # caused a brief flash of incorrect phonemes at the splice entry.
                orig_end_s = o_s + int(orig_dur * n_sr)
                if orig_end_s <= len(normal_audio):
                    normal_audio[o_s:orig_end_s] = 0.0
                elif o_s < len(normal_audio):
                    normal_audio[o_s:] = 0.0

                o_e = o_s + len(compressed)
                if o_e > len(normal_audio):
                    normal_audio = np.pad(normal_audio, (0, o_e - len(normal_audio)))

                # Fade in from silence (no bleed from wrong content)
                _xfade_splice = int(0.020 * n_sr)
                xf = min(_xfade_splice, len(compressed))
                if xf > 0:
                    ramp = np.linspace(0.0, 1.0, xf, dtype=np.float32)
                    normal_audio[o_s:o_s + xf] = compressed[:xf] * ramp
                    normal_audio[o_s + xf:o_e] = compressed[xf:]
                else:
                    normal_audio[o_s:o_e] = compressed

            sf.write(ds_out_wav, normal_audio, n_sr)
            print(f"  Rap pass complete.")

    _stop_here("infer")

    # ── Stage 6.5: RVC chain — DiffSinger (Ikura voice) -> Koroki via Korokiv5 ──
    if not args.no_rvc_chain:
        print("  RVC chain (Korokiv5): converting DiffSinger output to Koroki voice...")
        koroki_wav = str(work_dir / "synth_koroki.wav")
        if _run_rvc_chain(ds_out_wav, koroki_wav):
            ds_out_wav = koroki_wav
            print("  RVC chain complete.")
        else:
            print("  RVC chain failed — mixing raw DiffSinger output.")

    # ── Stage 7: Mix ─────────────────────────────────────────────────────────
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    instr_offset = float(state.get("instr_offset", 0.0))

    if effective_key_shift != 0:
        shifted_instr = str(work_dir / "instr_shifted.wav")
        print(f"  Pitch-shifting instrumental by {effective_key_shift:+d} semitones...")
        _pitch_shift_wav(no_vocals_path, shifted_instr, effective_key_shift)
        no_vocals_path = shifted_instr
    _mix(ds_out_wav, no_vocals_path, output_path,
         instr_offset_s=instr_offset, vocal_gain=args.vocal_gain)
    shutil.copy2(output_path, work_dir / "output.wav")
    print(f"\nDone! Output: {output_path}")
    print(f"  Work dir (all stages): {work_dir}")


if __name__ == "__main__":
    main()
