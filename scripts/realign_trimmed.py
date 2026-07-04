"""
Realign transcriptions for trimmed segments + verify against song lyrics.

Pipeline per trimmed segment:
  1. Cross-correlate trimmed wav against pre-trim original → exact trim window
  2. Truncate original ph_seq + ph_dur arithmetically to match new boundaries
  3. Verify against Genius lyrics: phonemize song lyrics, find best matching
     window for the truncated phonemes. Confidence < THRESHOLD → flag for review.

Non-trimmed kept segments: original transcriptions pass through unchanged
(RVC didn't alter timing or phoneme content).

Output:
  data/diffsinger_raw/koroki_singing_v2/<singer>/transcriptions.csv (final)
  data/diffsinger_raw/koroki_singing_v2/<singer>/review_needed.txt
    — list of flagged trimmed segments to listen to + decide

Run (uses .venv_diffsinger because pyopenjtalk lives there):
    .venv_diffsinger\\Scripts\\python.exe scripts\\realign_trimmed.py
    .venv_diffsinger\\Scripts\\python.exe scripts\\realign_trimmed.py --singer yoasobi
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

import numpy as np
import librosa
import scipy.signal

# Make lyrics_align importable
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "experiments" / "diffsinger"))
from lyrics_align import fetch_genius_lyrics, _to_hiragana  # noqa: E402

CORPUS_DIR = REPO_ROOT / "data" / "diffsinger_raw" / "koroki_singing_v2"
SOURCE_DIRS = {
    "yoasobi": REPO_ROOT / "data" / "diffsinger_raw" / "yoasobi",
    "ado": REPO_ROOT / "data" / "diffsinger_raw" / "ado",
}

# Confidence threshold for lyric match — below this is flagged
LYRIC_MATCH_THRESHOLD = 0.80

# Cache for fetched lyrics (per song key)
_LYRIC_CACHE_PATH = CORPUS_DIR / ".lyric_cache.json"

# IPA → SOFA-format (inverse of _SOFA_TO_IPA in align_staging.py, plus extras).
# pyopenjtalk also outputs SOFA-style format, so we compare both in this space.
_IPA_TO_SOFA: dict[str, str] = {
    "a": "a", "i": "i", "ɯ": "u", "u": "u", "e": "e", "o": "o",
    "k": "k", "ɡ": "g", "g": "g", "s": "s", "z": "z",
    "t": "t", "d": "d", "n": "n", "h": "h",
    "b": "b", "p": "p", "m": "m", "j": "y", "y": "y",
    "ɾ": "r", "r": "r", "w": "w", "ɴ": "N", "N": "N", "c": "cl",
    "ɕ": "sh", "sh": "sh", "tɕ": "ch", "ch": "ch", "ts": "ts",
    "ɸ": "f", "f": "f", "dʑ": "j", "v": "v",
    "AP": "AP", "SP": "SP", "EP": "SP", "GS": "SP",
}

# Phonemes to ignore in matching (silence, breath markers, etc.)
_IGNORE_PHONEMES = {"AP", "SP", "EP", "br", "pau", "sil", "GS", "cl"}

# Phoneme equivalence classes for fuzzy matching. pyopenjtalk and the corpus's
# SOFA used slightly different conventions — devoiced vowels (I/U), palatalized
# variants (ky, ny), affricate spellings (ch/ts/tɕ). Normalize them all to a
# common bucket so the lyric-match score reflects real similarity.
_PHONEME_NORMALIZE: dict[str, str] = {
    # Devoiced vowels → plain
    "I": "i", "U": "u", "A": "a", "E": "e", "O": "o",
    # Palatalized consonants → base
    "ky": "k", "gy": "g", "ny": "n", "hy": "h", "my": "m",
    "ry": "r", "by": "b", "py": "p", "sy": "s",
    # Affricate variants → ts
    "tɕ": "ts", "ch": "ts",
    # Sibilants
    "ɕ": "sh", "ʃ": "sh", "sh": "sh",
    # Voiced affricates / fricatives
    "dʑ": "j", "ʒ": "j",
    # Fricative variants
    "ɸ": "f",
    # Pitch nasal
    "ɴ": "N",
}


def normalize_phoneme(p: str) -> str:
    return _PHONEME_NORMALIZE.get(p, p)


def ipa_to_sofa_seq(ph_seq_str: str) -> list[str]:
    """Convert space-separated IPA ph_seq to SOFA-format phoneme list."""
    return [_IPA_TO_SOFA.get(p, p) for p in ph_seq_str.split()]


# ── Cross-correlation: find exact trim boundaries ────────────────────────

def find_trim_boundaries(trimmed_wav: Path, original_wav: Path,
                          sr_target: int = 16000) -> tuple[float, float]:
    """Use cross-correlation to find where trimmed audio sits in original.

    Returns (start_sec, end_sec) — the offset within the original where the
    trimmed audio starts/ends. Sample-precise alignment because both files are
    bit-identical subset audio (we sliced from original, didn't re-process).
    """
    y_trim, sr = librosa.load(str(trimmed_wav), sr=sr_target, mono=True)
    y_orig, _ = librosa.load(str(original_wav), sr=sr_target, mono=True)

    # Use scipy.signal.correlate in valid mode — finds best match position
    corr = scipy.signal.correlate(y_orig, y_trim, mode="valid")
    start_sample = int(np.argmax(corr))
    end_sample = start_sample + len(y_trim)

    return start_sample / sr_target, end_sample / sr_target


# ── Truncate ph_seq + ph_dur to match trim window ────────────────────────

def truncate_transcription(ph_seq_str: str, ph_dur_str: str,
                            trim_start: float, trim_end: float,
                            min_overlap_frac: float = 0.5) -> tuple[str, str]:
    """Trim ph_seq + ph_dur arithmetically. Keep phonemes >=50% inside window."""
    phs = ph_seq_str.split()
    durs = [float(d) for d in ph_dur_str.split()]

    new_phs = []
    new_durs = []
    cumul = 0.0
    for ph, dur in zip(phs, durs):
        ph_start = cumul
        ph_end = cumul + dur
        cumul = ph_end

        # Compute overlap with trim window
        ov_start = max(ph_start, trim_start)
        ov_end = min(ph_end, trim_end)
        ov = max(0.0, ov_end - ov_start)

        # Keep phoneme if majority of it is in the window
        if dur > 0 and ov / dur >= min_overlap_frac:
            new_phs.append(ph)
            # Use full original duration (don't clip to window — DiffSinger
            # works with consistent ph_dur where sum ≈ audio duration; small
            # discrepancies tolerated)
            new_durs.append(round(ov, 4))

    return " ".join(new_phs), " ".join(f"{d}" for d in new_durs)


# ── Lyric verification ───────────────────────────────────────────────────

def load_lyric_cache() -> dict[str, dict]:
    if _LYRIC_CACHE_PATH.exists():
        try:
            return json.loads(_LYRIC_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_lyric_cache(cache: dict) -> None:
    _LYRIC_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LYRIC_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                                   encoding="utf-8")


def fetch_song_phonemes(song_key: str, cache: dict) -> list[str] | None:
    """For song key like 'yoasobi_idol' or 'ado_新時代' — return SOFA-style phonemes.

    Strips the singer prefix to get the song title, then includes artist name
    in the Genius query (bare "idol" returns wrong songs — need "idol YOASOBI").
    Caches result. Returns None if lyrics unavailable.
    """
    if song_key in cache:
        entry = cache[song_key]
        return entry["phonemes"] if entry.get("found") else None

    # Build search query — include artist for disambiguation
    if "_" in song_key:
        singer, title = song_key.split("_", 1)
        query = f"{title} {singer.upper()}"
    else:
        query = song_key

    print(f"  [lyrics] fetching '{query}' from Genius…")
    lyrics_lines = fetch_genius_lyrics(query)

    if not lyrics_lines:
        cache[song_key] = {"found": False, "phonemes": None}
        save_lyric_cache(cache)
        return None

    # Phonemize line-by-line — pyopenjtalk can hang on certain long inputs
    try:
        import pyopenjtalk
        phonemes: list[str] = []
        for line in lyrics_lines:
            if not line.strip():
                continue
            try:
                ph_str = pyopenjtalk.g2p(line)
                phonemes.extend(ph_str.split())
            except Exception as inner:
                print(f"    pyopenjtalk skipped line ({inner}): {line[:40]}")
                continue
    except Exception as exc:
        print(f"    pyopenjtalk failed for {query}: {exc}")
        cache[song_key] = {"found": False, "phonemes": None}
        save_lyric_cache(cache)
        return None

    cache[song_key] = {"found": True, "phonemes": phonemes}
    save_lyric_cache(cache)
    print(f"    got {len(phonemes)} phonemes from lyrics")
    return phonemes


def filter_meaningful(phonemes: list[str]) -> list[str]:
    """Drop silence/breath markers + normalize for fuzzy matching."""
    return [normalize_phoneme(p) for p in phonemes if p not in _IGNORE_PHONEMES]


def collapse_repeats(phonemes: list[str]) -> list[str]:
    """Collapse consecutive duplicate phonemes (long vowels: 'o o' → 'o').

    pyopenjtalk emits separate tokens for long vowels (オー → 'o o') while
    SOFA-style transcriptions may have only 'o:'. Normalize both to single.
    """
    out = []
    for p in phonemes:
        if not out or out[-1] != p:
            out.append(p)
    return out


def find_best_match(seg_phs: list[str], song_phs: list[str]) -> tuple[float, int]:
    """Longest-matching-subsequence search via difflib.

    Returns (match_score 0..1, position). Score is the fraction of segment
    phonemes covered by the longest contiguous matching block between segment
    and full song. Tolerates insertions/deletions, which exact-window scoring
    misses (e.g., singer dropping a 'wo' particle = 1 phoneme shift in a 30+
    phoneme segment, which a fixed-window scorer would degrade to ~50%).
    """
    import difflib

    seg = collapse_repeats(filter_meaningful(seg_phs))
    song = collapse_repeats(filter_meaningful(song_phs))
    n = len(seg)
    if n == 0 or len(song) == 0:
        return 0.0, -1

    sm = difflib.SequenceMatcher(None, seg, song, autojunk=False)
    # Sum up sizes of all matching blocks — captures distributed matches
    # (e.g., 90% match with one phoneme inserted in the middle)
    blocks = sm.get_matching_blocks()
    total_matched = sum(b.size for b in blocks)

    # Also get the single longest block for the position
    if blocks:
        biggest = max(blocks, key=lambda b: b.size)
        best_pos = biggest.b
    else:
        best_pos = -1

    score = total_matched / n
    return score, best_pos


# ── Main: process a singer's corpus ──────────────────────────────────────

def process_singer(singer: str, lyric_cache: dict) -> None:
    print(f"\n{'='*72}")
    print(f"Singer: {singer}")
    print(f"{'='*72}")

    singer_dir = CORPUS_DIR / singer
    wavs_dir = singer_dir / "wavs"
    originals_dir = singer_dir / "wavs_originals"
    source_dir = SOURCE_DIRS.get(singer)
    if not source_dir or not source_dir.exists():
        print(f"  ERROR: no source dir for {singer} at {source_dir}")
        return
    if not wavs_dir.exists():
        print(f"  ERROR: no wavs dir at {wavs_dir}")
        return

    # Load source transcriptions (the original alignments)
    source_csv = source_dir / "transcriptions.csv"
    if not source_csv.exists():
        print(f"  ERROR: no source transcriptions at {source_csv}")
        return

    source_rows: dict[str, dict] = {}
    with source_csv.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            source_rows[row["name"]] = row

    print(f"  loaded {len(source_rows)} source transcriptions")

    # Categorize current kept files
    kept_files = sorted(wavs_dir.glob("*.wav"))
    trimmed_files: list[Path] = []
    untouched_files: list[Path] = []
    for w in kept_files:
        original = originals_dir / w.name
        if original.exists():
            trimmed_files.append(w)
        else:
            untouched_files.append(w)

    print(f"  kept files: {len(kept_files)} total ({len(untouched_files)} untouched, "
          f"{len(trimmed_files)} trimmed)")

    out_rows: list[dict] = []
    flagged: list[dict] = []

    # Untouched: pass through original transcriptions verbatim
    skipped = 0
    for w in untouched_files:
        stem = w.stem
        src = source_rows.get(stem)
        if not src:
            skipped += 1
            continue
        out_rows.append(src)
    if skipped:
        print(f"  WARNING: {skipped} untouched files have no source transcription")

    # Trimmed: realign + verify
    for i, w in enumerate(trimmed_files, 1):
        stem = w.stem
        src = source_rows.get(stem)
        if not src:
            print(f"  [{i}/{len(trimmed_files)}] {stem}: NO SOURCE TRANSCRIPTION — skip")
            continue
        original = originals_dir / w.name

        # 1. Find trim boundaries
        try:
            tr_start, tr_end = find_trim_boundaries(w, original)
        except Exception as exc:
            print(f"  [{i}/{len(trimmed_files)}] {stem}: cross-correlation failed: {exc}")
            flagged.append({"name": stem, "reason": f"correlate_fail:{exc}", "score": 0.0})
            continue

        # 2. Truncate transcriptions to new boundaries
        new_ph_seq, new_ph_dur = truncate_transcription(
            src["ph_seq"], src["ph_dur"], tr_start, tr_end
        )

        if not new_ph_seq:
            print(f"  [{i}/{len(trimmed_files)}] {stem}: realignment produced empty ph_seq")
            flagged.append({"name": stem, "reason": "empty_after_trim", "score": 0.0})
            continue

        # 3. Lyric verification
        song_key = stem.rsplit("_seg", 1)[0]
        song_phs = fetch_song_phonemes(song_key, lyric_cache)

        if song_phs is None:
            # No lyrics available — flag with reason but still accept the realignment
            print(f"  [{i}/{len(trimmed_files)}] {stem}: trim [{tr_start:.2f}-{tr_end:.2f}] "
                  f"no lyrics, accepting (flagged)")
            out_rows.append({"name": stem, "ph_seq": new_ph_seq, "ph_dur": new_ph_dur})
            flagged.append({"name": stem, "reason": "no_lyrics", "score": -1.0})
            continue

        seg_sofa = ipa_to_sofa_seq(new_ph_seq)
        score, pos = find_best_match(seg_sofa, song_phs)
        verdict = "✓" if score >= LYRIC_MATCH_THRESHOLD else "?"
        print(f"  [{i}/{len(trimmed_files)}] {stem}: trim [{tr_start:.2f}-{tr_end:.2f}] "
              f"{len(new_ph_seq.split())} phonemes  lyric_match={score:.2f} {verdict}")

        out_rows.append({"name": stem, "ph_seq": new_ph_seq, "ph_dur": new_ph_dur})
        if score < LYRIC_MATCH_THRESHOLD:
            flagged.append({"name": stem, "reason": "low_lyric_match",
                            "score": round(score, 3)})

    # Write final transcriptions.csv
    out_csv = singer_dir / "transcriptions.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "ph_seq", "ph_dur"])
        writer.writeheader()
        for row in out_rows:
            writer.writerow({k: row[k] for k in ["name", "ph_seq", "ph_dur"]})

    print(f"\n  wrote {len(out_rows)} rows to {out_csv}")

    # Write review_needed.txt
    if flagged:
        review_path = singer_dir / "review_needed.txt"
        lines = [
            f"# Trimmed segments flagged for manual review",
            f"# Lyric match threshold: {LYRIC_MATCH_THRESHOLD}",
            f"# Reasons: low_lyric_match | no_lyrics | empty_after_trim | correlate_fail",
            f"# Listen to each, decide if the transcription is acceptable.",
            f"# To drop a flagged segment: delete it from wavs/ and re-run script.",
            "",
        ]
        for fl in flagged:
            lines.append(f"  {fl['name']}  reason={fl['reason']}  score={fl['score']}")
        review_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"  {len(flagged)} segments flagged — see {review_path}")
    else:
        print(f"  no flags — all trimmed segments verified clean against lyrics")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--singer", default=None,
                         help="process only one singer (default: all)")
    args = parser.parse_args()

    lyric_cache = load_lyric_cache()
    targets = [args.singer] if args.singer else list(SOURCE_DIRS.keys())

    for singer in targets:
        if singer not in SOURCE_DIRS:
            print(f"Skipping unknown singer: {singer}")
            continue
        process_singer(singer, lyric_cache)

    print(f"\n{'='*72}")
    print("Lyric cache saved at:", _LYRIC_CACHE_PATH)
    print("Done.")


if __name__ == "__main__":
    main()
