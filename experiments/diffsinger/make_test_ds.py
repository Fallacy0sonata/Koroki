"""
Create a test .ds file from a validation clip for DiffSinger inference.

Reads ph_seq/ph_dur from transcriptions.csv, extracts F0 with parselmouth,
writes a .ds file ready for: python scripts/infer.py acoustic test.ds --exp koroki_ja

Run from Koroki root:
    .venv_diffsinger\Scripts\python.exe experiments\diffsinger\make_test_ds.py
    .venv_diffsinger\Scripts\python.exe experiments\diffsinger\make_test_ds.py --clip 0717
"""

from __future__ import annotations

import argparse
import csv
import json
import numpy as np
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_F0_TIMESTEP = 0.005  # 5ms, matches DiffSinger default


def _extract_f0(wav_path: str, total_dur: float) -> list[float]:
    import parselmouth
    sound = parselmouth.Sound(wav_path)
    pitch = sound.to_pitch(time_step=_F0_TIMESTEP, pitch_floor=65.0, pitch_ceiling=1100.0)

    n_frames = int(round(total_dur / _F0_TIMESTEP))
    f0_values = []
    for i in range(n_frames):
        t = i * _F0_TIMESTEP
        val = pitch.get_value_at_time(t)
        f0_values.append(0.0 if (val is None or np.isnan(val)) else float(val))

    # Linear interpolation over unvoiced frames so model has smooth pitch curve
    arr = np.array(f0_values)
    voiced = arr > 0
    if voiced.any():
        xs = np.arange(len(arr))
        arr = np.interp(xs, xs[voiced], arr[voiced])

    return [round(float(v), 1) for v in arr]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clip", default="0678", help="Clip ID to use (e.g. 0678)")
    parser.add_argument("--out", default=None, help="Output .ds path")
    args = parser.parse_args()

    csv_path = _REPO_ROOT / "data" / "diffsinger_raw" / "japanese" / "transcriptions.csv"
    wav_dir = _REPO_ROOT / "data" / "diffsinger_raw" / "japanese" / "wavs"
    out_path = Path(args.out) if args.out else (
        _REPO_ROOT / "experiments" / "diffsinger" / "DiffSinger" / f"test_{args.clip}.ds"
    )

    # Find the clip row
    row = None
    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["name"] == args.clip:
                row = r
                break

    if row is None:
        print(f"Clip {args.clip} not found in transcriptions.csv")
        return

    ph_seq = row["ph_seq"].split()
    ph_dur = [float(d) for d in row["ph_dur"].split()]
    total_dur = sum(ph_dur)

    wav_path = str(wav_dir / f"{args.clip}.wav")
    print(f"Extracting F0 from {wav_path} ({total_dur:.2f}s)...")
    f0_seq = _extract_f0(wav_path, total_dur)

    ds = [{
        "offset": 0.0,
        "text": " ".join(ph_seq),
        "ph_seq": " ".join(ph_seq),
        "ph_dur": " ".join(str(round(d, 4)) for d in ph_dur),
        "f0_seq": " ".join(str(v) for v in f0_seq),
        "f0_timestep": str(_F0_TIMESTEP),
    }]

    out_path.write_text(json.dumps(ds, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Written: {out_path}")
    print(f"Phonemes: {len(ph_seq)}  |  Duration: {total_dur:.2f}s  |  F0 frames: {len(f0_seq)}")
    print(f"\nRun inference from experiments/diffsinger/DiffSinger/:")
    print(f"  python scripts/infer.py acoustic test_{args.clip}.ds --exp koroki_ja")


if __name__ == "__main__":
    main()
