import csv
path = "C:/Users/Shinn/Desktop/Koroki/data/diffsinger_raw/japanese/transcriptions.csv"
with open(path, encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
yoasobi = [r for r in rows if "yoasobi_idol" in r["name"]]
print(f"Total rows: {len(rows)}")
print(f"koroki_yoasobi_idol_ entries: {len(yoasobi)}")
if yoasobi:
    print("First:", yoasobi[0]["name"])
    print("Last:", yoasobi[-1]["name"])
