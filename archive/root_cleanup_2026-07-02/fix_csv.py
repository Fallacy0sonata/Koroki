import csv, io

path = "C:/Users/Shinn/Desktop/Koroki/data/diffsinger_raw/japanese/transcriptions.csv"
with open(path, encoding="utf-8-sig") as f:
    content = f.read()

reader = csv.DictReader(io.StringIO(content))
fields_raw = reader.fieldnames
fields = [f.strip('"') for f in fields_raw]
print("Fields:", fields)

rows = []
for row in reader:
    rows.append({fields[i]: v for i, v in enumerate(row.values())})
print(f"Rows read: {len(rows)}")

with open(path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["name", "ph_seq", "ph_dur"])
    writer.writeheader()
    writer.writerows(rows)
print("CSV rewritten cleanly.")
