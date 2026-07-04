"""
SOFA-align the 798 staging segments in data/diffsinger_staging/japanese/.

These are actual Koroki-covers-of-YOASOBI singing recordings (vc_*.wav) with
raw Japanese text labels (.lab). Running SOFA on them produces phoneme timings,
making them usable as DiffSinger training data.

This is higher-quality training data than CosyVoice patterns because it contains
real singing dynamics (sustained vowels, melodic F0, breath patterns).

Usage (from Koroki root, using .venv_diffsinger):
    .venv_diffsinger\\Scripts\\python.exe experiments\\diffsinger\\align_staging.py

Output: data/diffsinger_raw/staging_aligned/
    wavs/<name>.wav        (symlinked or copied from staging)
    transcriptions.csv
    phonemes.txt

NOTE: Segments >20s may have lower SOFA alignment accuracy.
      The --max-dur flag (default 20s) skips overly long segments.
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import sys
from pathlib import Path

import soundfile as sf

_SELF_DIR    = Path(__file__).resolve().parent
_REPO_ROOT   = _SELF_DIR.parents[1]
_SOFA_DIR    = _SELF_DIR / "SOFA"
_SOFA_CKPT   = _SOFA_DIR / "checkpoints" / "japanese" / "step.100000.ckpt"
_SOFA_DICT   = _SOFA_DIR / "checkpoints" / "japanese" / "japanese-extension-sofa.txt"

_STAGING_DIR = _REPO_ROOT / "data" / "diffsinger_staging" / "japanese"
_OUT_DIR     = _REPO_ROOT / "data" / "diffsinger_raw" / "staging_aligned"

# SOFA romaji → DiffSinger IPA (matches yoasobi/patterns phoneme inventory)
_SOFA_TO_IPA: dict[str, str] = {
    "a": "a",   "i": "i",   "u": "ɯ",   "e": "e",   "o": "o",
    "k": "k",   "g": "ɡ",   "s": "s",   "z": "z",
    "t": "t",   "d": "d",   "n": "n",   "h": "h",
    "b": "b",   "p": "p",   "m": "m",   "y": "j",   "r": "ɾ",
    "w": "w",   "N": "ɴ",   "cl": "c",
    "sh": "ɕ",  "ch": "tɕ", "ts": "ts",
    "f": "ɸ",   "j": "dʑ",   "v": "v",
    "ky": "k",  "gy": "ɡ",  "ny": "n",  "hy": "h",
    "my": "m",  "ry": "ɾ",  "by": "b",  "py": "p",
    "AP": "AP", "SP": "SP", "br": "AP",
    "pau": "SP", "sil": "SP", "EP": "SP", "GS": "SP",
}

_BATCH_SIZE = 100  # process in batches to avoid SOFA memory issues on large sets


def _parse_textgrid(path: Path) -> list[tuple[float, float, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
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


def _textgrid_to_row(name: str, tg_path: Path) -> tuple[str, str] | None:
    intervals = _parse_textgrid(tg_path)
    if not intervals:
        return None
    ph_seq, ph_dur = [], []
    for xmin, xmax, label in intervals:
        if xmax <= xmin:
            continue
        ipa = _SOFA_TO_IPA.get(label, label)
        if not ipa:
            continue
        ph_seq.append(ipa)
        ph_dur.append(str(round(xmax - xmin, 4)))
    if not ph_seq:
        return None
    return " ".join(ph_seq), " ".join(ph_dur)


def _run_sofa_batch(batch_dir: Path) -> None:
    cmd = [
        sys.executable, str(_SOFA_DIR / "infer.py"),
        "--ckpt",       str(_SOFA_CKPT),
        "--folder",     str(batch_dir),
        "--dictionary", str(_SOFA_DICT),
        "--out_formats", "TextGrid",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            cwd=str(_SOFA_DIR), timeout=3600)
    for line in result.stderr.splitlines():
        if line.strip() and "warning" not in line.lower():
            print(f"  [sofa] {line.strip()}")
    if result.returncode != 0:
        print(f"  WARN: SOFA batch failed (exit {result.returncode})")
        print(result.stderr[-400:])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-dur", type=float, default=20.0,
                        help="Skip segments longer than this many seconds (default: 20)")
    parser.add_argument("--out-dir", type=Path, default=_OUT_DIR)
    args = parser.parse_args()

    if not _SOFA_CKPT.exists():
        sys.exit(f"SOFA checkpoint not found: {_SOFA_CKPT}")
    if not _SOFA_DICT.exists():
        sys.exit(f"SOFA dictionary not found: {_SOFA_DICT}")
    if not _STAGING_DIR.exists():
        sys.exit(f"Staging directory not found: {_STAGING_DIR}")

    sys.path.insert(0, str(_SELF_DIR))
    from sofa_runner import _kana_to_sofa_lab  # type: ignore

    out_dir  = args.out_dir
    wavs_dir = out_dir / "wavs"
    sofa_tmp = out_dir / "_sofa_tmp"
    wavs_dir.mkdir(parents=True, exist_ok=True)
    sofa_tmp.mkdir(parents=True, exist_ok=True)

    # Collect all staging segments
    wav_files = sorted(_STAGING_DIR.glob("vc_*.wav"))
    print(f"Found {len(wav_files)} staging WAVs")

    # Filter by duration
    entries: list[tuple[str, Path, str]] = []  # (name, wav_path, text)
    skipped_dur = skipped_lab = 0

    for wav_path in wav_files:
        lab_path = wav_path.with_suffix(".lab")
        if not lab_path.exists():
            skipped_lab += 1
            continue

        try:
            info = sf.info(str(wav_path))
            dur = info.frames / info.samplerate
        except Exception:
            skipped_lab += 1
            continue

        if dur > args.max_dur:
            skipped_dur += 1
            continue

        text = lab_path.read_text(encoding="utf-8").strip()
        if not text:
            skipped_lab += 1
            continue

        entries.append((wav_path.stem, wav_path, text))

    print(f"  Using: {len(entries)} segments")
    print(f"  Skipped (too long >{args.max_dur}s): {skipped_dur}")
    print(f"  Skipped (missing/empty lab): {skipped_lab}")

    if not entries:
        sys.exit("No valid segments to process")

    # Process in batches
    n_batches = (len(entries) + _BATCH_SIZE - 1) // _BATCH_SIZE
    all_rows: list[tuple[str, str, str]] = []
    total_ok = total_fail = 0

    for batch_idx in range(n_batches):
        batch = entries[batch_idx * _BATCH_SIZE : (batch_idx + 1) * _BATCH_SIZE]
        batch_dir = sofa_tmp / f"batch_{batch_idx:03d}"
        batch_dir.mkdir(exist_ok=True)
        (batch_dir / "TextGrid").mkdir(exist_ok=True)

        print(f"\n[Batch {batch_idx+1}/{n_batches}] {len(batch)} segments — preparing...")

        prepared = []
        for name, wav_path, text in batch:
            lab_text = _kana_to_sofa_lab(text)
            if not lab_text.strip():
                continue
            shutil.copy2(wav_path, batch_dir / f"{name}.wav")
            (batch_dir / f"{name}.lab").write_text(lab_text, encoding="utf-8")
            prepared.append(name)

        if not prepared:
            print("  No valid labs in batch, skipping")
            continue

        print(f"  Running SOFA on {len(prepared)} segments...")
        _run_sofa_batch(batch_dir)

        tg_dir = batch_dir / "TextGrid"
        ok = fail = 0
        for name in prepared:
            tg_path = tg_dir / f"{name}.TextGrid"
            if not tg_path.exists():
                candidates = sorted(tg_dir.glob(f"{name}*.TextGrid"))
                tg_path = candidates[0] if candidates else None

            if tg_path is None:
                fail += 1
                continue

            row = _textgrid_to_row(name, tg_path)
            if row is None:
                fail += 1
                continue

            ph_seq, ph_dur = row
            all_rows.append((name, ph_seq, ph_dur))
            ok += 1

        total_ok += ok
        total_fail += fail
        print(f"  Batch result: {ok} OK, {fail} failed")

    # Copy WAVs and write transcriptions.csv
    print(f"\nCopying {total_ok} WAVs to output dir...")
    for name, _, _ in entries:
        row_names = {r[0] for r in all_rows}
        if name not in row_names:
            continue
        src = _STAGING_DIR / f"{name}.wav"
        dst = wavs_dir / f"{name}.wav"
        if not dst.exists():
            shutil.copy2(src, dst)

    csv_path = out_dir / "transcriptions.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(("name", "ph_seq", "ph_dur"))
        writer.writerows(all_rows)

    print(f"Wrote {total_ok} rows to {csv_path} ({total_fail} failed)")

    # Copy phonemes.txt
    ph_src = _REPO_ROOT / "data" / "diffsinger_raw" / "yoasobi" / "phonemes.txt"
    ph_dst = out_dir / "phonemes.txt"
    if ph_src.exists():
        shutil.copy2(ph_src, ph_dst)
        print(f"Copied phonemes.txt")

    print(f"\nDone. Dataset written to: {out_dir}")
    print("Next steps:")
    print("  Binarize + train with koroki_v2.yaml (includes this dataset)")


if __name__ == "__main__":
    main()
