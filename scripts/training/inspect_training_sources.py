from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(r"C:/Users/Shinn/Desktop/Koroki/Koroki - Copy")
FILES = [
    "supervised_training.json",
    "simulated_training.json",
    "slm_training_simulated.json",
    "koroki_training_data.json",
]

for name in FILES:
    path = ROOT / name
    if not path.exists():
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"FILE {name}: ERROR {exc}")
        continue

    kind = type(data).__name__
    length = len(data) if hasattr(data, "__len__") else "n/a"
    print(f"FILE {name}: type={kind} len={length}")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            print("  first_keys:", list(first.keys())[:20])
        else:
            print("  first_item_type:", type(first).__name__)
    elif isinstance(data, dict):
        print("  top_keys:", list(data.keys())[:20])
