"""
Batch-convert YOASOBI vocal segments → Koroki voice using Applio RVC.

Source:  data/diffsinger_raw/yoasobi/wavs/*.wav   (300 real Ikura singing clips)
Output:  data/diffsinger_raw/koroki_rvc/wavs/*.wav (same clips in Koroki's voice)

After conversion, copies transcriptions.csv (same phoneme alignment, same
filenames) and phonemes.txt (63-phoneme set from patterns_full) so the
koroki_rvc dataset is immediately ready to binarize.

Why this works: Koroki ≈ 90% Ikura — RVC voice contamination from the source
singer is effectively minimal and desirable. The output is REAL SINGING, unlike
all previous CosyVoice-based training data which is speech prosody only.

Usage (from Koroki root, any Python — no special venv needed):
    python experiments/diffsinger/convert_yoasobi_rvc.py
    python experiments/diffsinger/convert_yoasobi_rvc.py --dry-run
    python experiments/diffsinger/convert_yoasobi_rvc.py --transpose -2
    python experiments/diffsinger/convert_yoasobi_rvc.py --model Korokiv3
    python experiments/diffsinger/convert_yoasobi_rvc.py --index-rate 0.3
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT  = Path(__file__).resolve().parents[2]
_APPLIO_DIR = _REPO_ROOT / "ApplioV3.6.2"
_APPLIO_PY  = _APPLIO_DIR / "env" / "python.exe"
_APPLIO_CORE = _APPLIO_DIR / "core.py"

_YOASOBI_DIR     = _REPO_ROOT / "data" / "diffsinger_raw" / "yoasobi"
_PATTERNS_FULL_DIR = _REPO_ROOT / "data" / "diffsinger_raw" / "patterns_full"
_OUT_DIR         = _REPO_ROOT / "data" / "diffsinger_raw" / "koroki_rvc"

_MODELS = {
    "Korokiv4": (
        _REPO_ROOT / "adapters" / "singing" / "Korokiv4_500e_25000s.pth",
        _REPO_ROOT / "adapters" / "singing" / "Korokiv4.index",
    ),
    "Korokiv3": (
        _REPO_ROOT / "adapters" / "singing" / "Korokiv3_400e_10400s.pth",
        _REPO_ROOT / "adapters" / "singing" / "Korokiv3.index",
    ),
    "Korokiv2": (
        _REPO_ROOT / "adapters" / "singing" / "Korokiv2_400e_8000s.pth",
        _REPO_ROOT / "adapters" / "singing" / "Korokiv2.index",
    ),
}


def _convert_one(
    wav_in: Path,
    wav_out: Path,
    pth: Path,
    index: Path,
    transpose: int,
    index_rate: float,
    dry_run: bool,
) -> bool:
    """Run Applio RVC on a single wav. Returns True on success."""
    if dry_run:
        print(f"  [dry-run] {wav_in.name} → {wav_out.name}")
        return True

    if wav_out.exists() and wav_out.stat().st_size > 0:
        return True  # already done, skip

    wav_out.parent.mkdir(parents=True, exist_ok=True)

    # Applio sometimes ignores the output path and writes next to the input.
    # Use a temp output inside Applio dir to keep things clean, then move.
    with tempfile.NamedTemporaryFile(
        suffix=".wav", dir=_APPLIO_DIR, delete=False
    ) as tmp:
        tmp_out = Path(tmp.name)

    cmd = [
        str(_APPLIO_PY),
        str(_APPLIO_CORE),
        "infer",
        "--input_path",          str(wav_in),
        "--output_path",         str(tmp_out),
        "--pth_path",            str(pth),
        "--index_path",          str(index),
        "--pitch",               str(transpose),
        "--f0_method",           "rmvpe",
        "--index_rate",          str(index_rate),
        "--volume_envelope",     "1.0",
        "--protect",             "0.33",
        "--split_audio",         "False",
        "--f0_autotune",         "False",
        "--f0_autotune_strength","0.0",
        "--clean_audio",         "True",
        "--clean_strength",      "0.7",
        "--export_format",       "WAV",
        "--embedder_model",      "contentvec",
        "--formant_shifting",    "False",
        "--post_process",        "False",
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(_APPLIO_DIR),
    )

    # Always print stdout/stderr for diagnosis (can be suppressed later)
    if result.stdout.strip():
        for line in result.stdout.strip().splitlines():
            print(f"    [applio] {line}")
    if result.stderr.strip():
        for line in result.stderr.strip().splitlines()[-10:]:  # last 10 lines
            print(f"    [applio:err] {line}")

    # Find actual output — Applio may write to tmp_out or to a sibling file
    output_found: Path | None = None
    if tmp_out.exists() and tmp_out.stat().st_size > 0:
        output_found = tmp_out
    else:
        # Scan Applio dir for any new wav (last resort)
        candidates = [
            p for p in _APPLIO_DIR.glob("*.wav")
            if p != tmp_out and p.stat().st_size > 0
        ]
        if candidates:
            output_found = max(candidates, key=lambda p: p.stat().st_mtime)

    if output_found is None:
        tmp_out.unlink(missing_ok=True)
        print(f"  FAIL {wav_in.name}: Applio produced no output (rc={result.returncode})")
        return False

    shutil.move(str(output_found), str(wav_out))
    tmp_out.unlink(missing_ok=True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch RVC conversion: YOASOBI vocals → Koroki voice"
    )
    parser.add_argument(
        "--model",
        choices=list(_MODELS.keys()),
        default="Korokiv4",
        help="Which Koroki RVC model to use (default: Korokiv4)",
    )
    parser.add_argument(
        "--transpose", type=int, default=0,
        help="Pitch shift in semitones (default: 0)",
    )
    parser.add_argument(
        "--index-rate", type=float, default=0.4,
        help="Index influence 0–1 (default: 0.4)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be done without running Applio",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=_OUT_DIR,
        help=f"Output dataset directory (default: {_OUT_DIR})",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Only convert first N files (0 = all). Useful for quick comparison.",
    )
    args = parser.parse_args()

    out_dir  = args.out_dir
    wavs_out = out_dir / "wavs"
    pth, index = _MODELS[args.model]

    # Sanity checks
    if not args.dry_run:
        if not _APPLIO_PY.exists():
            sys.exit(f"Applio Python not found: {_APPLIO_PY}")
        if not _APPLIO_CORE.exists():
            sys.exit(f"Applio core.py not found: {_APPLIO_CORE}")
        if not pth.exists():
            sys.exit(f"RVC model not found: {pth}")
        if not index.exists():
            sys.exit(f"RVC index not found: {index}")

    wavs_in = sorted((_YOASOBI_DIR / "wavs").glob("*.wav"))
    if not wavs_in:
        sys.exit(f"No wavs found in {_YOASOBI_DIR / 'wavs'}")
    if args.limit > 0:
        wavs_in = wavs_in[:args.limit]

    wavs_out.mkdir(parents=True, exist_ok=True)

    already_done = sum(
        1 for w in wavs_in
        if (wavs_out / w.name).exists() and (wavs_out / w.name).stat().st_size > 0
    )

    print(f"Model:      {args.model} ({pth.name})")
    print(f"Transpose:  {args.transpose} semitones")
    print(f"Index rate: {args.index_rate}")
    print(f"Total wavs: {len(wavs_in)}  (already done: {already_done})")
    print(f"Output:     {out_dir}")
    print()

    ok = fail = skip = 0
    for i, wav_in in enumerate(wavs_in, 1):
        wav_out = wavs_out / wav_in.name
        if wav_out.exists() and wav_out.stat().st_size > 0 and not args.dry_run:
            skip += 1
            continue
        print(f"  [{i}/{len(wavs_in)}] {wav_in.name}", end="", flush=True)
        success = _convert_one(
            wav_in, wav_out, pth, index,
            args.transpose, args.index_rate, args.dry_run,
        )
        if success:
            ok += 1
            print(" ✓")
        else:
            fail += 1

    print(f"\nConverted: {ok}  Skipped: {skip}  Failed: {fail}")

    if fail > 0:
        print(f"WARNING: {fail} files failed — check output above")

    if args.dry_run:
        return

    # Copy transcriptions.csv (same filenames → alignment is reusable directly)
    src_csv = _YOASOBI_DIR / "transcriptions.csv"
    dst_csv = out_dir / "transcriptions.csv"
    if src_csv.exists():
        shutil.copy(src_csv, dst_csv)
        print(f"Copied transcriptions.csv ({src_csv} → {dst_csv})")
    else:
        print(f"WARNING: transcriptions.csv not found at {src_csv}")

    # Copy phonemes.txt from patterns_full (full 63-phoneme set)
    src_ph = _PATTERNS_FULL_DIR / "phonemes.txt"
    dst_ph = out_dir / "phonemes.txt"
    if src_ph.exists():
        shutil.copy(src_ph, dst_ph)
        print(f"Copied phonemes.txt (63-phoneme set) → {dst_ph}")
    else:
        print(f"WARNING: phonemes.txt not found at {src_ph}")
        print("  Copy manually from data/diffsinger_raw/patterns_full/phonemes.txt")

    print(f"\nDone. koroki_rvc dataset ready at: {out_dir}")
    print("Next steps:")
    print("  1. Listen to a few wavs in koroki_rvc/wavs/ to verify quality")
    print("  2. Create koroki_v6.yaml using koroki_rvc + patterns_full datasets")
    print("  3. Binarize and train")


if __name__ == "__main__":
    main()
