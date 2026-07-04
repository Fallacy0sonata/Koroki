"""
Generate clean Koroki singing training data from synced lyrics + Fish Speech.

Pipeline:
  1. syncedlyrics — fetch timestamped lyrics by song query (no audio download needed)
  2. Fish Speech  — synthesize each lyric line in Koroki's voice (Japanese, voice-cloned)
  3. SOFA         — align phonemes in the Fish Speech clips
  4. Save         — WAV + (ph_seq, ph_dur) into DiffSinger training set

Why this beats the old vc_*.wav pipeline (YOASOBI → RVC → Koroki):
  RVC voice conversion introduces high-note artifacts that DiffSinger learns as
  Koroki's identity. Fish Speech generates clean, artifact-free Koroki phonemes
  from real voice samples. DiffSinger learns Koroki's timbre from the speech
  clips; at inference time the singing F0 and phoneme timing are specified by
  SOFA alignment of the real song vocal.

Requirements:
    Fish Speech adapter running: scripts\\easy_start_fishspeech_adapter.ps1
    (fish-speech-1.5, port 9003)

Usage (from Koroki root, .venv_diffsinger):
    .venv_diffsinger\\Scripts\\python.exe experiments/diffsinger/gen_koroki_singing_data.py "yoasobi idol"
    .venv_diffsinger\\Scripts\\python.exe experiments/diffsinger/gen_koroki_singing_data.py "yoasobi idol" --resume
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).parent))
from sing_song import (  # type: ignore
    _resample, _normalize_phone, _sofa_available,
    _parse_textgrid_sofa, _normalize_transcript,
    _REPO_ROOT, _SELF_DIR, _SILENCE_LABELS,
)
from sofa_runner import _kana_to_sofa_lab  # type: ignore

_TARGET_SR  = 44100  # Hz — DiffSinger training WAV sample rate
_MIN_PHONES = 4      # skip segments with fewer real phonemes
_MIN_DUR    = 0.5    # skip segments shorter than this (seconds)
_MAX_EDGE_AP = 0.10  # cap leading/trailing silence in labels


# ---------------------------------------------------------------------------
# IndexTTS HTTP client
# ---------------------------------------------------------------------------

def _to_hiragana(text: str) -> str:
    """Convert Japanese text (kanji/kana mix) to hiragana before sending to IndexTTS.
    IndexTTS is a Chinese TTS model — it reads CJK kanji as Chinese.
    Hiragana is unambiguously Japanese and forces correct Japanese pronunciation."""
    try:
        import pykakasi
        kks = pykakasi.kakasi()
        result = kks.convert(text)
        return "".join(item["hira"] or item["orig"] for item in result)
    except ImportError:
        print("  WARN: pykakasi not installed — kanji will be read as Chinese")
        print("  Install: .venv_diffsinger\\Scripts\\pip install pykakasi")
        return text


_MIN_VOICE_RMS = 0.005  # clips below this RMS are silence/noise-only, skip them
_MIN_CLIP_SECONDS = 0.5  # clips shorter than this are useless for alignment


def _call_indextts(text: str, url: str) -> tuple[np.ndarray, int] | None:
    try:
        import requests
    except ImportError:
        sys.exit("requests not installed — run: .venv_diffsinger\\Scripts\\pip install requests")
    try:
        resp = requests.post(f"{url}/synthesize", json={"text": text}, timeout=90)
        resp.raise_for_status()
        wav_bytes = base64.b64decode(resp.json()["wav_base64"])
        audio, sr = sf.read(io.BytesIO(wav_bytes))
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        audio = audio.astype(np.float32)

        # Reject clips with no real voice energy (CosyVoice failure modes:
        # pure tapping, silence, or music with no speech)
        rms = float(np.sqrt(np.mean(audio ** 2)))
        duration = len(audio) / sr
        if rms < _MIN_VOICE_RMS:
            print(f"  [tts] rejected: RMS {rms:.4f} below threshold — no voice detected")
            return None
        if duration < _MIN_CLIP_SECONDS:
            print(f"  [tts] rejected: clip too short ({duration:.2f}s)")
            return None

        return audio, int(sr)
    except Exception as e:
        print(f"  [indextts] call failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Phoneme labels from SOFA TextGrid
# ---------------------------------------------------------------------------

def _textgrid_to_labels(
    tg_path: str,
    total_dur: float,
) -> tuple[list[str], list[float]] | None:
    """
    Convert a SOFA TextGrid into (ph_seq, ph_dur).
    Returns None if result has too few real phonemes.
    """
    raw = _parse_textgrid_sofa(tg_path)
    if not raw:
        return None

    ph_seq: list[str] = []
    ph_dur: list[float] = []
    first = True

    for xmin, xmax, label in raw:
        d = round(xmax - xmin, 4)
        if d < 0.003:
            continue
        is_sil = label in _SILENCE_LABELS or label in ("AP", "SP")
        if is_sil:
            ph = "AP" if first else "SP"
        else:
            ph = _normalize_phone(label)
            first = False
        if ph_seq and ph_seq[-1] == ph and ph in ("AP", "SP"):
            ph_dur[-1] = round(ph_dur[-1] + d, 4)
        else:
            ph_seq.append(ph)
            ph_dur.append(d)

    real = [p for p in ph_seq if p not in ("AP", "SP")]
    if len(real) < _MIN_PHONES:
        return None

    # Cap edge silences
    if ph_seq and ph_seq[0] in ("AP", "SP") and ph_dur[0] > _MAX_EDGE_AP:
        ph_dur[0] = _MAX_EDGE_AP
    if ph_seq and ph_seq[-1] in ("AP", "SP") and ph_dur[-1] > _MAX_EDGE_AP:
        ph_dur[-1] = _MAX_EDGE_AP

    return ph_seq, ph_dur


def _uniform_labels(lab_text: str, total_dur: float) -> tuple[list[str], list[float]] | None:
    """Fallback when SOFA fails: uniform duration per phoneme from the lab file."""
    phones = [p for p in lab_text.strip().split() if p.strip() and p not in ("SP", "AP")]
    if len(phones) < _MIN_PHONES:
        return None
    dur = round(total_dur / len(phones), 4)
    return phones, [dur] * len(phones)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Koroki training data via IndexTTS + SOFA (no WORLD, no RVC)"
    )
    parser.add_argument("query", help="Song query for lyrics search (e.g. 'yoasobi idol')")
    parser.add_argument("--out-speaker", default="koroki",
                        help="Speaker name prefix for output files (default: koroki)")
    parser.add_argument("--training-dir", default=None, metavar="DIR",
                        help="Training data root (default: data/diffsinger_raw/japanese/)")
    parser.add_argument("--tts-url", default="http://127.0.0.1:9003",
                        help="TTS adapter URL (default: http://127.0.0.1:9003 — Fish Speech)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip IndexTTS generation for clips already saved in work dir")
    parser.add_argument("--max-clips", type=int, default=None, metavar="N",
                        help="Stop after generating N clips (for quick quality tests)")
    args = parser.parse_args()

    # Sanity checks before doing any slow work
    try:
        import requests
        r = requests.get(f"{args.tts_url}/ready", timeout=5)
        if r.status_code != 200:
            sys.exit(f"TTS adapter not ready at {args.tts_url} — run: scripts\\easy_start_fishspeech_adapter.ps1")
    except Exception:
        sys.exit(f"TTS adapter unreachable at {args.tts_url} — run: scripts\\easy_start_fishspeech_adapter.ps1")

    if not _sofa_available():
        sys.exit("SOFA checkpoint not found — cannot align phonemes")

    train_dir = Path(args.training_dir) if args.training_dir else (
        _REPO_ROOT / "data" / "diffsinger_raw" / "japanese"
    )
    wavs_dir = train_dir / "wavs"
    wavs_dir.mkdir(parents=True, exist_ok=True)
    csv_path = train_dir / "transcriptions.csv"

    slug = re.sub(r"[^\w\-]", "_", args.query)[:50].strip("_")
    work_dir = _REPO_ROOT / "data" / "diffsinger_work" / slug
    work_dir.mkdir(parents=True, exist_ok=True)

    lyrics_p = work_dir / "lyrics.json"

    # ── Stage 1: Fetch synced lyrics ─────────────────────────────────────────
    if args.resume and lyrics_p.exists() and lyrics_p.stat().st_size > 0:
        print("  [resume] lyrics")
        lyric_segs = json.loads(lyrics_p.read_text(encoding="utf-8"))
    else:
        try:
            import syncedlyrics
        except ImportError:
            sys.exit("syncedlyrics not installed — run: .venv_diffsinger\\Scripts\\pip install syncedlyrics")
        print("  Searching for synced lyrics...")
        lrc = syncedlyrics.search(args.query)
        if not lrc:
            sys.exit(f"No synced lyrics found for '{args.query}'")
        raw: list[tuple[float, str]] = []
        for line in lrc.splitlines():
            m = re.match(r'\[(\d+):(\d+(?:\.\d+)?)\](.*)', line.strip())
            if not m:
                continue
            mins, secs, text = m.groups()
            t = int(mins) * 60 + float(secs)
            text = re.sub(r'<\d+:\d+(?:\.\d+)?>', '', text).strip()
            if text:
                raw.append((t, text))
        if not raw:
            sys.exit("Lyrics found but no timed lines could be parsed")
        lyric_segs = []
        for i, (t, text) in enumerate(raw):
            end = raw[i + 1][0] if i + 1 < len(raw) else t + 5.0
            lyric_segs.append({
                "start": t, "end": end,
                "text": _normalize_transcript(text),
            })
        lyrics_p.write_text(json.dumps(lyric_segs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {len(lyric_segs)} lyric lines")

    # ── Stage 2: Generate IndexTTS clips ─────────────────────────────────────
    tts_dir = work_dir / "tts_clips"
    tts_dir.mkdir(exist_ok=True)
    (tts_dir / "TextGrid").mkdir(exist_ok=True)

    existing_names: set[str] = set()
    if csv_path.exists():
        with csv_path.open(encoding="utf-8") as f:
            existing_names = {row["name"] for row in csv.DictReader(f)}

    out_slug = re.sub(r"[^\w]+", "_", args.query[:30]).strip("_").lower()

    clip_meta: list[dict] = []
    print("\n  Generating TTS clips...")
    for idx, seg in enumerate(lyric_segs):
        if args.max_clips is not None and len(clip_meta) >= args.max_clips:
            print(f"  [max-clips {args.max_clips} reached — stopping early]")
            break
        text = seg["text"].strip()
        if not text:
            continue

        lab = _kana_to_sofa_lab(text)
        if not lab.strip():
            continue

        item_id = f"{args.out_speaker}_{out_slug}_{idx:04d}"
        clip_wav = tts_dir / f"line_{idx:04d}.wav"
        lab_file = tts_dir / f"line_{idx:04d}.lab"

        # Write lab file (always — even on resume, SOFA needs it)
        lab_file.write_text(lab, encoding="utf-8")

        if args.resume and clip_wav.exists() and clip_wav.stat().st_size > 0:
            print(f"  [{idx:03d}] [resume] {clip_wav.name}")
        else:
            # Convert kanji→hiragana for TTS: CosyVoice (and IndexTTS) are
            # Chinese-primary models that read raw kanji as Chinese. Hiragana
            # is phonetically unambiguous Japanese. SOFA gets original text.
            tts_text = _to_hiragana(text)
            print(f"  [{idx:03d}] '{text[:50]}'...", end=" ", flush=True)
            result = _call_indextts(tts_text, args.tts_url)
            if result is None:
                print("FAILED — skipping")
                continue
            tts_audio, tts_sr = result
            if tts_sr != _TARGET_SR:
                tts_audio = _resample(tts_audio, tts_sr, _TARGET_SR)
            sf.write(str(clip_wav), tts_audio, _TARGET_SR, subtype="PCM_16")
            print("done")

        clip_meta.append({"idx": idx, "item_id": item_id, "text": text})

    if not clip_meta:
        sys.exit("No clips generated — check IndexTTS and lyrics")

    # ── Stage 3: SOFA alignment on IndexTTS clips ────────────────────────────
    _sofa_dir = _SELF_DIR / "SOFA"
    _ckpt = _sofa_dir / "checkpoints" / "japanese" / "step.100000.ckpt"
    _dict = _sofa_dir / "checkpoints" / "japanese" / "japanese-extension-sofa.txt"

    print(f"\n  Running SOFA on {len(clip_meta)} IndexTTS clips...")
    sofa_cmd = [
        sys.executable, str(_sofa_dir / "infer.py"),
        "--ckpt", str(_ckpt),
        "--folder", str(tts_dir),
        "--dictionary", str(_dict),
        "--out_formats", "TextGrid",
    ]
    sofa_result = subprocess.run(sofa_cmd, capture_output=True, text=True,
                                 cwd=str(_sofa_dir), timeout=600)
    if sofa_result.returncode != 0:
        print(f"  WARN: SOFA exited {sofa_result.returncode} — will use uniform-duration fallback")

    # ── Stage 4: Build training entries ──────────────────────────────────────
    print("\n  Building training entries...")
    new_rows: list[dict] = []
    skipped = 0

    for meta in clip_meta:
        idx     = meta["idx"]
        item_id = meta["item_id"]

        if item_id in existing_names:
            print(f"  [{idx:03d}] already in CSV — skipping")
            continue

        clip_wav = tts_dir / f"line_{idx:04d}.wav"
        if not clip_wav.exists():
            skipped += 1
            continue

        audio, sr = sf.read(str(clip_wav))
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        total_dur = len(audio) / sr

        if total_dur < _MIN_DUR:
            skipped += 1
            continue

        # SOFA TextGrid (prefer) → uniform fallback
        tg_path = tts_dir / "TextGrid" / f"line_{idx:04d}.TextGrid"
        labels = None
        if tg_path.exists():
            labels = _textgrid_to_labels(str(tg_path), total_dur)
        if labels is None:
            lab_text = (tts_dir / f"line_{idx:04d}.lab").read_text(encoding="utf-8")
            labels = _uniform_labels(lab_text, total_dur)
        if labels is None:
            skipped += 1
            continue

        ph_seq, ph_dur = labels

        # Copy clip to training wavs
        shutil.copy2(str(clip_wav), str(wavs_dir / f"{item_id}.wav"))
        new_rows.append({
            "name":   item_id,
            "ph_seq": " ".join(ph_seq),
            "ph_dur": " ".join(str(d) for d in ph_dur),
        })
        existing_names.add(item_id)
        print(f"  [{idx:03d}] {item_id}  {len([p for p in ph_seq if p not in ('AP','SP')])} phones  {total_dur:.1f}s")

    # ── Append to transcriptions.csv ─────────────────────────────────────────
    if new_rows:
        write_header = not csv_path.exists() or csv_path.stat().st_size == 0
        with csv_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["name", "ph_seq", "ph_dur"])
            if write_header:
                writer.writeheader()
            writer.writerows(new_rows)
        print(f"\n  {len(new_rows)} segments → {train_dir}")
        if skipped:
            print(f"  {skipped} skipped (too short, bad alignment, or already present)")
        print(f"\n  Re-binarize to include these in training:")
        print(f"    .venv_diffsinger\\Scripts\\python DiffSinger\\scripts\\binarize.py --config configs/<name>.yaml")
    else:
        print("\n  No new segments written.")


if __name__ == "__main__":
    main()
