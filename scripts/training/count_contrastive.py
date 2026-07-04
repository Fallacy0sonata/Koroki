from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

path = Path(r"C:/Users/Shinn/Desktop/Koroki/data/training/lora/contrastive_bad_good.jsonl")
counts = Counter()
total = 0
for line in path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    row = json.loads(line)
    counts[row.get("tier", "unknown")] += 1
    total += 1

print("contrastive_total", total)
print("contrastive_by_tier", dict(counts))
