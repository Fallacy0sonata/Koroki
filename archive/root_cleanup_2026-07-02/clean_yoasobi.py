import csv, shutil
from pathlib import Path

ROOT = Path("C:/Users/Shinn/Desktop/Koroki")
csv_path = ROOT / "data/diffsinger_raw/japanese/transcriptions.csv"
wavs_dir = ROOT / "data/diffsinger_raw/japanese/wavs"
work_dir = ROOT / "data/diffsinger_work/yoasobi_idol"

# Remove all yoasobi_idol entries from CSV
with open(csv_path, encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

before = len(rows)
keep = [r for r in rows if "yoasobi_idol" not in r["name"]]
removed_csv = before - len(keep)

with open(csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["name", "ph_seq", "ph_dur"])
    writer.writeheader()
    writer.writerows(keep)
print(f"CSV: removed {removed_csv} rows, {len(keep)} remain")

# Remove any yoasobi_idol wavs from training dir
wavs = list(wavs_dir.glob("*yoasobi_idol*.wav"))
for w in wavs:
    w.unlink()
print(f"Wavs: removed {len(wavs)} files")

# Wipe entire work dir so clips regenerate cleanly
if work_dir.exists():
    shutil.rmtree(work_dir)
    print(f"Work dir deleted: {work_dir}")
else:
    print("Work dir not found (already clean)")

print("Done. Ready for fresh gen run.")
