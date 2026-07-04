"""
One-shot script: sets up data/diffsinger_raw/yoasobi/ with the 123 real
YOASOBI samples already in data/diffsinger_raw/japanese/.

Run from the Koroki root:
  .venv_diffsinger\Scripts\python scripts\setup_yoasobi_dataset.py
"""
import csv
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR   = REPO_ROOT / "data" / "diffsinger_raw" / "japanese"
DST_DIR   = REPO_ROOT / "data" / "diffsinger_raw" / "yoasobi"
SRC_WAVS  = SRC_DIR / "wavs"
DST_WAVS  = DST_DIR / "wavs"

DST_WAVS.mkdir(parents=True, exist_ok=True)

# Copy phonemes.txt (same IPA set)
shutil.copy2(SRC_DIR / "phonemes.txt", DST_DIR / "phonemes.txt")

src_csv = SRC_DIR / "transcriptions.csv"
dst_csv = DST_DIR / "transcriptions.csv"

copied_wavs = 0
rows_written = 0
skipped_missing = 0

existing_names: set[str] = set()
if dst_csv.exists():
    with dst_csv.open(encoding="utf-8") as f:
        existing_names = {r["name"] for r in csv.DictReader(f)}

new_rows = []
with src_csv.open(encoding="utf-8") as f:
    for row in csv.DictReader(f):
        if not row["name"].startswith("yoasobi_"):
            continue
        if row["name"] in existing_names:
            continue
        src_wav = SRC_WAVS / f"{row['name']}.wav"
        if not src_wav.exists():
            print(f"  MISSING wav: {src_wav.name}")
            skipped_missing += 1
            continue
        dst_wav = DST_WAVS / src_wav.name
        if not dst_wav.exists():
            shutil.copy2(src_wav, dst_wav)
            copied_wavs += 1
        new_rows.append(row)

write_header = not dst_csv.exists() or dst_csv.stat().st_size == 0
with dst_csv.open("a", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["name", "ph_seq", "ph_dur"])
    if write_header:
        writer.writeheader()
    writer.writerows(new_rows)
    rows_written = len(new_rows)

total = rows_written + len(existing_names)
print(f"Done.")
print(f"  Copied wavs   : {copied_wavs}")
print(f"  CSV rows added: {rows_written}  (already present: {len(existing_names)})")
print(f"  Total samples : {total}")
if skipped_missing:
    print(f"  Missing wavs  : {skipped_missing} (check source dir)")
print(f"\nNext: run extract_yoasobi_songs.ps1 to add more songs, then binarize.")
