"""
Build DiffSinger acoustic model dataset from MFA TextGrid alignment output.

Parses MFA TextGrid files (phoneme intervals) and writes the transcriptions.csv
that DiffSinger's binarizer expects, then copies the 16kHz WAV files.

Input:
  data/mfa_textgrids/japanese/   MFA TextGrid files
  data/mfa_input/japanese/       16 kHz WAV files

Output:
  data/diffsinger_raw/japanese/
    wavs/               16 kHz WAV files (copied)
    transcriptions.csv  name, ph_seq, ph_dur

Run:
    .venv/Scripts/python.exe experiments/diffsinger/build_dataset.py
    .venv/Scripts/python.exe experiments/diffsinger/build_dataset.py --lang english
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# MFA silence labels — map all to DiffSinger's SP
_SILENCE = {"", "sil", "spn", "sp", "SIL", "SPN", "SP", "<eps>"}

# Minimum phoneme duration to keep (shorter ones are merged into SP)
_MIN_DUR = 0.01


def _parse_textgrid(path: Path) -> list[tuple[float, float, str]]:
    """
    Parse an MFA TextGrid file and return the 'phones' tier as
    list of (xmin, xmax, label) tuples.
    """
    text = path.read_text(encoding="utf-8", errors="replace")

    # Find the phones tier
    # TextGrid has multiple tiers; we want the one named "phones"
    tier_blocks = re.split(r'item\s*\[\d+\]\s*:', text)

    phones_intervals: list[tuple[float, float, str]] = []

    for block in tier_blocks:
        if 'name = "phones"' not in block and "name = \"phones\"" not in block:
            # also try without quotes
            if "name = phones" not in block:
                continue

        # Extract all intervals
        interval_blocks = re.split(r'intervals\s*\[\d+\]\s*:', block)
        for ib in interval_blocks[1:]:  # skip the tier header
            xmin_m = re.search(r'xmin\s*=\s*([\d.eE+\-]+)', ib)
            xmax_m = re.search(r'xmax\s*=\s*([\d.eE+\-]+)', ib)
            text_m = re.search(r'text\s*=\s*"([^"]*)"', ib)
            if xmin_m and xmax_m and text_m:
                xmin = float(xmin_m.group(1))
                xmax = float(xmax_m.group(1))
                label = text_m.group(1).strip()
                phones_intervals.append((xmin, xmax, label))

    return phones_intervals


def _build_ph_seq_dur(intervals: list[tuple[float, float, str]]) -> tuple[list[str], list[float]]:
    """
    Convert raw MFA phone intervals to DiffSinger ph_seq and ph_dur lists.
    - Silence labels → SP
    - Consecutive SP entries are merged
    - Leading/trailing SP are kept (DiffSinger uses them as padding)
    """
    ph_seq: list[str] = []
    ph_dur: list[float] = []

    for xmin, xmax, label in intervals:
        dur = round(xmax - xmin, 6)
        if dur < _MIN_DUR:
            continue

        ph = "SP" if label in _SILENCE else label

        if ph == "SP" and ph_seq and ph_seq[-1] == "SP":
            ph_dur[-1] += dur  # merge consecutive silences
        else:
            ph_seq.append(ph)
            ph_dur.append(dur)

    # Round durations
    ph_dur = [round(d, 4) for d in ph_dur]
    return ph_seq, ph_dur


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", default="japanese", choices=["japanese", "english"])
    parser.add_argument("--val-count", type=int, default=20,
                        help="Number of clips to reserve for validation (listed in output)")
    args = parser.parse_args()

    tg_dir = _REPO_ROOT / "data" / "mfa_textgrids" / args.lang
    # Use original 44100Hz clips (not the 16kHz MFA-input copies) — DiffSinger vocoder is 44.1kHz
    wav_src = _REPO_ROOT / "data" / "singing_training" / "clips"
    out_dir = _REPO_ROOT / "data" / "diffsinger_raw" / args.lang
    wavs_dir = out_dir / "wavs"
    wavs_dir.mkdir(parents=True, exist_ok=True)

    tg_files = sorted(tg_dir.glob("*.TextGrid"), key=lambda p: p.stem)
    if not tg_files:
        print(f"No TextGrid files found in {tg_dir}")
        return

    rows: list[dict] = []
    skipped = 0

    for tg_path in tg_files:
        name = tg_path.stem
        wav_path = wav_src / f"{name}.wav"
        if not wav_path.exists():
            print(f"  WARN: WAV not found for {name}, skipping")
            skipped += 1
            continue

        intervals = _parse_textgrid(tg_path)
        if not intervals:
            print(f"  WARN: empty phones tier in {tg_path.name}, skipping")
            skipped += 1
            continue

        ph_seq, ph_dur = _build_ph_seq_dur(intervals)

        if len(ph_seq) < 2:
            skipped += 1
            continue

        # Copy WAV
        shutil.copy2(str(wav_path), str(wavs_dir / f"{name}.wav"))

        rows.append({
            "name": name,
            "ph_seq": " ".join(ph_seq),
            "ph_dur": " ".join(str(d) for d in ph_dur),
        })

    # Collect all unique phonemes for info
    all_phonemes: set[str] = set()
    for row in rows:
        all_phonemes.update(row["ph_seq"].split())
    all_phonemes.discard("SP")

    # Write transcriptions.csv
    csv_path = out_dir / "transcriptions.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "ph_seq", "ph_dur"])
        writer.writeheader()
        writer.writerows(rows)

    # Write phoneme list (needed for DiffSinger config)
    phonemes_sorted = sorted(all_phonemes)
    ph_list_path = out_dir / "phonemes.txt"
    ph_list_path.write_text("\n".join(phonemes_sorted), encoding="utf-8")

    # Print a suggested val_prefixes list (last N clips)
    val_names = [r["name"] for r in rows[-args.val_count:]]

    print(f"\nDone.")
    print(f"  Language    : {args.lang}")
    print(f"  Clips       : {len(rows)} written, {skipped} skipped")
    print(f"  Phonemes    : {len(phonemes_sorted)} unique  →  {ph_list_path}")
    print(f"  CSV         : {csv_path}")
    print(f"  WAVs        : {wavs_dir}")
    print(f"\n  Suggested validation clips (last {args.val_count}):")
    print(f"    {val_names}")


if __name__ == "__main__":
    main()
