"""
Creates data/diffsinger_raw/koroki_clean/ from data/diffsinger_raw/japanese/
with yoasobi_*.wav entries excluded.

Uses NTFS hard links — no extra disk space required.
"""

import csv
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "diffsinger_raw" / "japanese"
DST = ROOT / "data" / "diffsinger_raw" / "koroki_clean"

src_csv = SRC / "transcriptions.csv"
src_wavs = SRC / "wavs"
dst_wavs = DST / "wavs"
dst_csv = DST / "transcriptions.csv"

if not src_csv.exists():
    print(f"ERROR: {src_csv} not found")
    sys.exit(1)

dst_wavs.mkdir(parents=True, exist_ok=True)

with open(src_csv, newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

koroki_rows = [r for r in rows if not r["name"].startswith("yoasobi_")]
excluded = len(rows) - len(koroki_rows)

linked, missing = 0, 0
for row in koroki_rows:
    name = row["name"]
    src_wav = src_wavs / f"{name}.wav"
    dst_wav = dst_wavs / f"{name}.wav"
    if not src_wav.exists():
        print(f"  WARN: missing wav {src_wav}")
        missing += 1
        continue
    if dst_wav.exists():
        dst_wav.unlink()
    os.link(src_wav, dst_wav)
    linked += 1

with open(dst_csv, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=koroki_rows[0].keys())
    writer.writeheader()
    writer.writerows(koroki_rows)

print(f"Done.")
print(f"  Excluded {excluded} yoasobi_*.wav entries")
print(f"  Linked {linked} Koroki wav files → {dst_wavs}")
if missing:
    print(f"  WARNING: {missing} wav files were missing from source")
print(f"  Written: {dst_csv}")
