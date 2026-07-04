"""
Singer pipeline — measure / test / convert / curate.

Per-singer workflow for building the koroki_singing_v2 corpus.
See docs/master_queue.md → "Singing Pipeline Reset (2026-06-21)" for context.

Source layout (each singer):
    data/diffsinger_raw/<singer>/
        wavs/*.wav         — raw vocal segments (real singing)
        transcriptions.csv — phoneme alignment (carried over verbatim)

Output layout:
    data/diffsinger_raw/koroki_singing_v2/<singer>/
        wavs/*.wav         — RVC + mixed Koroki singing
        transcriptions.csv — copy of source (same filenames)
        meta.json          — ratio used, RVC model, generated_at

Test outputs (small batches for listening):
    data/singing_test/<singer>/
        <segname>_ratio<X>.wav — one per (segment, ratio) combination

Subcommands:
    measure <singer>           — Resemblyzer cosine similarity to Koroki centroid
    test <singer> [opts]       — small batch RVC + mix at multiple ratios
    convert <singer> [opts]    — full batch RVC + mix at chosen ratio
    curate <singer>            — manifest-based interactive curation helper

Run from Koroki root with the main venv:
    .venv\\Scripts\\python.exe scripts\\singer_pipeline.py <subcommand> [args]
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable

import librosa
import numpy as np
import soundfile as sf

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s singer_pipeline: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("singer")

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "diffsinger_raw"
CORPUS_DIR = RAW_DIR / "koroki_singing_v2"
TEST_DIR = REPO_ROOT / "data" / "singing_test"

# Applio paths (same as convert_yoasobi_rvc.py)
APPLIO_DIR = REPO_ROOT / "ApplioV3.6.2"
APPLIO_PY = APPLIO_DIR / "env" / "python.exe"
APPLIO_CORE = APPLIO_DIR / "core.py"

RVC_MODELS = {
    "Korokiv4": (
        REPO_ROOT / "adapters" / "singing" / "Korokiv4_500e_25000s.pth",
        REPO_ROOT / "adapters" / "singing" / "Korokiv4.index",
    ),
    "Korokiv3": (
        REPO_ROOT / "adapters" / "singing" / "Korokiv3_400e_10400s.pth",
        REPO_ROOT / "adapters" / "singing" / "Korokiv3.index",
    ),
    "Korokiv2": (
        REPO_ROOT / "adapters" / "singing" / "Korokiv2_400e_8000s.pth",
        REPO_ROOT / "adapters" / "singing" / "Korokiv2.index",
    ),
}

# Koroki reference voice samples (used to build the Koroki centroid for measurement)
KOROKI_REFERENCE_SAMPLES = [
    REPO_ROOT / "voice_samples" / "EN_sample.wav",
    REPO_ROOT / "voice_samples" / "JP_sample1.wav",
]


# ── Resemblyzer-based measurement ────────────────────────────────────────

def _embed(encoder, wav_path: Path) -> np.ndarray:
    from resemblyzer import preprocess_wav
    return encoder.embed_utterance(preprocess_wav(str(wav_path)))


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def cmd_measure(args: argparse.Namespace) -> None:
    """Measure source singer similarity to Koroki centroid."""
    from resemblyzer import VoiceEncoder

    singer = args.singer
    wavs_dir = RAW_DIR / singer / "wavs"
    if not wavs_dir.exists():
        sys.exit(f"No wavs directory at: {wavs_dir}")

    all_wavs = sorted(wavs_dir.glob("*.wav"))
    if not all_wavs:
        sys.exit(f"No wavs found in: {wavs_dir}")

    # Stride through corpus to get variety across songs
    n = min(args.sample_size, len(all_wavs))
    step = max(1, len(all_wavs) // n)
    sample = all_wavs[::step][:n]

    logger.info("Measuring %s (%d sample wavs out of %d)", singer, len(sample), len(all_wavs))
    encoder = VoiceEncoder()

    # Koroki centroid (average over reference voice samples)
    koroki_embs = [_embed(encoder, p) for p in KOROKI_REFERENCE_SAMPLES]
    koroki_centroid = np.mean(koroki_embs, axis=0)

    # Singer samples
    singer_embs = []
    singer_self_sims = []
    for p in sample:
        singer_embs.append(_embed(encoder, p))
    singer_centroid = np.mean(singer_embs, axis=0)

    for i in range(len(singer_embs)):
        for j in range(i + 1, len(singer_embs)):
            singer_self_sims.append(_cosine(singer_embs[i], singer_embs[j]))

    # Cross similarities
    cross = [_cosine(se, ke) for se in singer_embs for ke in koroki_embs]
    centroid_sim = _cosine(singer_centroid, koroki_centroid)

    print()
    print(f"  Singer self-similarity (across samples): mean={np.mean(singer_self_sims):.3f}  "
          f"range={np.min(singer_self_sims):.3f}-{np.max(singer_self_sims):.3f}")
    print(f"  Koroki vs {singer} (individual):         mean={np.mean(cross):.3f}  "
          f"range={np.min(cross):.3f}-{np.max(cross):.3f}")
    print(f"  Koroki vs {singer} (centroid-vs-centroid): {centroid_sim:.3f}")

    print()
    threshold = args.threshold
    if centroid_sim >= threshold:
        verdict = f"PASS — centroid similarity {centroid_sim:.3f} >= threshold {threshold} → good RVC candidate"
    else:
        verdict = f"FAIL — centroid similarity {centroid_sim:.3f} < threshold {threshold} → cross-contamination risk"
    print(f"VERDICT: {verdict}")


# ── RVC + mix ────────────────────────────────────────────────────────────

def _run_rvc(wav_in: Path, wav_out: Path, pth: Path, index: Path,
              index_rate: float = 0.4, transpose: int = 0,
              protect: float = 0.33, timeout: int = 120) -> bool:
    """Run Applio RVC on a single wav. Returns True on success."""
    # Applio runs with cwd=APPLIO_DIR so relative input paths break — resolve to absolute.
    wav_in = wav_in.resolve()
    wav_out = wav_out.resolve()
    wav_out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".wav", dir=APPLIO_DIR, delete=False) as tmp:
        tmp_out = Path(tmp.name)

    cmd = [
        str(APPLIO_PY), str(APPLIO_CORE), "infer",
        "--input_path", str(wav_in),
        "--output_path", str(tmp_out),
        "--pth_path", str(pth),
        "--index_path", str(index),
        "--pitch", str(transpose),
        "--f0_method", "rmvpe",
        "--index_rate", str(index_rate),
        "--volume_envelope", "1.0",
        "--protect", str(protect),
        "--split_audio", "False",
        "--f0_autotune", "False",
        "--f0_autotune_strength", "0.0",
        "--clean_audio", "True",
        "--clean_strength", "0.7",
        "--export_format", "WAV",
        "--embedder_model", "contentvec",
        "--formant_shifting", "False",
        "--post_process", "False",
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=str(APPLIO_DIR),
    )

    output_found: Path | None = None
    if tmp_out.exists() and tmp_out.stat().st_size > 0:
        output_found = tmp_out
    else:
        candidates = [
            p for p in APPLIO_DIR.glob("*.wav")
            if p != tmp_out and p.stat().st_size > 0
        ]
        if candidates:
            output_found = max(candidates, key=lambda p: p.stat().st_mtime)

    if output_found is None:
        tmp_out.unlink(missing_ok=True)
        logger.warning("RVC failed for %s (rc=%d): %s",
                       wav_in.name, result.returncode, result.stderr[-300:])
        return False

    shutil.move(str(output_found), str(wav_out))
    tmp_out.unlink(missing_ok=True)
    return True


def _mix_audio(rvc_path: Path, original_path: Path, output_path: Path, ratio: float) -> None:
    """Mix: final = ratio * rvc + (1 - ratio) * original. Ratio toward 1.0 = more Koroki."""
    rvc, sr_r = librosa.load(str(rvc_path), sr=None, mono=False)
    orig, sr_o = librosa.load(str(original_path), sr=None, mono=False)

    target_sr = sr_r  # RVC output rate wins
    if sr_o != target_sr:
        orig = librosa.resample(orig, orig_sr=sr_o, target_sr=target_sr)

    # Match shapes
    if rvc.ndim == 1: rvc = rvc[None, :]
    if orig.ndim == 1: orig = orig[None, :]
    target_len = min(rvc.shape[-1], orig.shape[-1])
    rvc = rvc[:, :target_len]
    orig = orig[:, :target_len]
    # Channel match
    if rvc.shape[0] != orig.shape[0]:
        if rvc.shape[0] == 1:
            rvc = np.repeat(rvc, orig.shape[0], axis=0)
        elif orig.shape[0] == 1:
            orig = np.repeat(orig, rvc.shape[0], axis=0)

    mix = ratio * rvc + (1.0 - ratio) * orig
    peak = float(np.max(np.abs(mix)))
    if peak > 1.0:
        mix = mix / peak * 0.97
    sf.write(str(output_path), mix.T if mix.shape[0] > 1 else mix[0], target_sr)


def _resolve_model(name: str) -> tuple[Path, Path]:
    if name not in RVC_MODELS:
        sys.exit(f"Unknown RVC model: {name}. Choose from {list(RVC_MODELS.keys())}")
    pth, index = RVC_MODELS[name]
    if not pth.exists() or not index.exists():
        sys.exit(f"Model files missing: {pth} or {index}")
    return pth, index


def cmd_test(args: argparse.Namespace) -> None:
    """Small batch — N segments × N index_rates for listening test.

    Sweeps RVC index_rate (how strongly the output matches the trained voice
    cloud). Higher = more recognizable Koroki, lower = more source-singer
    naturalness. No time-domain audio mixing (it produces phase artifacts).
    """
    singer = args.singer
    wavs_dir = RAW_DIR / singer / "wavs"
    if not wavs_dir.exists():
        sys.exit(f"No wavs directory at: {wavs_dir}")

    all_wavs = sorted(wavs_dir.glob("*.wav"))
    if not all_wavs:
        sys.exit(f"No wavs found in: {wavs_dir}")

    n = args.segments
    step = max(1, len(all_wavs) // n)
    chosen = all_wavs[::step][:n]
    index_rates = [float(r.strip()) for r in args.index_rates.split(",")]
    pth, index = _resolve_model(args.model)

    out_dir = TEST_DIR / singer
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Test batch: %d segments × %d index_rates = %d outputs",
                 len(chosen), len(index_rates), len(chosen) * len(index_rates))
    logger.info("Output dir: %s", out_dir)
    logger.info("Model: %s, transpose=%d, protect=%.2f",
                 args.model, args.transpose, args.protect)

    # Each (segment, index_rate) needs its own RVC pass — can't reuse
    for i, wav_in in enumerate(chosen, 1):
        base = wav_in.stem
        for ir in index_rates:
            out = out_dir / f"{base}__idx{int(ir*100):03d}.wav"
            if out.exists():
                logger.info("[%d/%d] idx%.2f cached: %s", i, len(chosen), ir, out.name)
                continue
            logger.info("[%d/%d] RVC idx=%.2f: %s", i, len(chosen), ir, wav_in.name)
            ok = _run_rvc(
                wav_in, out, pth, index,
                index_rate=ir, transpose=args.transpose,
                protect=args.protect,
            )
            if not ok:
                logger.warning("Skipped: %s @ idx=%.2f", wav_in.name, ir)

    print()
    print("Listen to outputs and decide which index_rate gives most Koroki-ness without artifacts.")
    print(f"Files written to: {out_dir}")
    print()
    print("Files:")
    for p in sorted(out_dir.glob("*__idx*.wav")):
        print(f"  {p.name}")
    print()
    print(f"Once decided, run: scripts/singer_pipeline.py convert {singer} --index-rate <R>")


def cmd_convert(args: argparse.Namespace) -> None:
    """Full batch — convert all wavs at chosen index_rate (pure RVC, no mix)."""
    singer = args.singer
    wavs_in_dir = RAW_DIR / singer / "wavs"
    if not wavs_in_dir.exists():
        sys.exit(f"No wavs directory at: {wavs_in_dir}")

    out_dir = CORPUS_DIR / singer
    wavs_out = out_dir / "wavs"
    wavs_out.mkdir(parents=True, exist_ok=True)

    pth, index = _resolve_model(args.model)

    wavs_in = sorted(wavs_in_dir.glob("*.wav"))
    if args.limit > 0:
        wavs_in = wavs_in[:args.limit]

    logger.info("Batch convert: %d wavs at index_rate %.2f", len(wavs_in), args.index_rate)
    logger.info("Model: %s, transpose=%d, protect=%.2f",
                 args.model, args.transpose, args.protect)
    logger.info("Output: %s", out_dir)

    ok = skip = fail = 0
    for i, wav_in in enumerate(wavs_in, 1):
        wav_out = wavs_out / wav_in.name
        if wav_out.exists() and wav_out.stat().st_size > 0:
            skip += 1
            continue
        logger.info("[%d/%d] %s", i, len(wavs_in), wav_in.name)
        if not _run_rvc(wav_in, wav_out, pth, index,
                         index_rate=args.index_rate, transpose=args.transpose,
                         protect=args.protect):
            fail += 1
            continue
        ok += 1

    # Copy transcriptions.csv
    src_csv = RAW_DIR / singer / "transcriptions.csv"
    dst_csv = out_dir / "transcriptions.csv"
    if src_csv.exists():
        shutil.copy(src_csv, dst_csv)
        logger.info("Copied transcriptions.csv")

    # Write meta.json
    meta = {
        "singer": singer,
        "model": args.model,
        "index_rate": args.index_rate,
        "transpose": args.transpose,
        "protect": args.protect,
        "total_segments": len(wavs_in),
        "converted": ok,
        "skipped_cached": skip,
        "failed": fail,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print()
    print(f"Converted: {ok}  Skipped (cached): {skip}  Failed: {fail}")
    print(f"Output: {out_dir}")
    print(f"Next step: scripts/singer_pipeline.py curate {singer}")


def _audit_segment(wav_path: Path) -> dict:
    """Compute quick audit signals for a converted segment.

    Heuristic flags (informational only — user decides):
      - duration: short segments (<2s) often capture only consonant/breath
      - silence_pct: high silence often indicates breath/no-vocal segment
      - f0_std_hz: low std hints at over-quantized pitch (autotune/synthesis tail)
      - flag: short summary, e.g. "SHORT", "SILENT", "FLATPITCH", or "" if normal

    None of these auto-reject — they sort/highlight segments to prioritize review.
    """
    try:
        y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
        duration = len(y) / sr

        # silence ratio: frames below -40 dBFS
        rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
        rms_db = 20 * np.log10(np.maximum(rms, 1e-10))
        silence_pct = float(np.mean(rms_db < -40) * 100)

        # F0 stats
        f0, voiced, _ = librosa.pyin(y, fmin=80, fmax=600, sr=sr, frame_length=2048)
        f0_clean = f0[voiced & ~np.isnan(f0)]
        f0_std = float(np.std(f0_clean)) if len(f0_clean) > 10 else 0.0

        flags = []
        if duration < 2.0:
            flags.append("SHORT")
        if silence_pct > 50:
            flags.append("SILENT")
        if 0 < f0_std < 12:
            flags.append("FLATPITCH")  # very narrow F0 range — possibly artifact

        return {
            "duration": duration,
            "silence_pct": silence_pct,
            "f0_std": f0_std,
            "flag": ",".join(flags) if flags else "",
        }
    except Exception as exc:
        return {"duration": 0.0, "silence_pct": 0.0, "f0_std": 0.0, "flag": f"ERR:{exc}"[:24]}


def _extract_song_key(filename: str) -> str:
    """Pull a sortable song key out of '<singer>_<song>_segNNNN.wav'."""
    parts = filename.rsplit("_seg", 1)
    return parts[0] if len(parts) == 2 else filename


def cmd_curate(args: argparse.Namespace) -> None:
    """Generate a curation manifest with audit metadata. User marks files to delete."""
    singer = args.singer
    out_dir = CORPUS_DIR / singer
    wavs_dir = out_dir / "wavs"
    if not wavs_dir.exists():
        sys.exit(f"No converted wavs at: {wavs_dir}")

    manifest_path = out_dir / "curate_manifest.txt"

    if args.apply:
        if not manifest_path.exists():
            sys.exit(f"No manifest at {manifest_path}. Run without --apply to create one.")
        deleted = kept = 0
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Parse "keep|drop <filename>" — filename is the second whitespace-delimited token
            parts = line.split()
            if len(parts) < 2:
                continue
            verdict = parts[0].lower()
            fname = parts[1]
            f = wavs_dir / fname
            if not f.exists():
                continue
            if verdict in ("drop", "delete", "x", "no"):
                f.unlink()
                deleted += 1
            else:
                kept += 1
        print(f"Curation applied: kept {kept}, deleted {deleted}")
        return

    # Generate manifest with audit hints
    wavs = sorted(wavs_dir.glob("*.wav"))
    logger.info("Auditing %d segments — this takes a moment per file...", len(wavs))

    audits = []
    for i, w in enumerate(wavs, 1):
        if i % 20 == 0:
            logger.info("  audited %d/%d", i, len(wavs))
        a = _audit_segment(w)
        a["name"] = w.name
        a["song"] = _extract_song_key(w.name)
        audits.append(a)

    # Sort by song, then by segment order
    audits.sort(key=lambda r: (r["song"], r["name"]))

    flagged = sum(1 for a in audits if a["flag"])

    lines = [
        f"# Curation manifest for {singer}",
        f"# Total: {len(audits)} segments, {flagged} flagged for prioritized review",
        "#",
        "# Format: <verdict> <filename>  # dur  silence%  f0std  [flags]",
        "# Change 'keep' to 'drop' for segments to delete on --apply.",
        "#",
        "# Flag meanings:",
        "#   SHORT     — segment is shorter than 2 seconds (low value)",
        "#   SILENT    — more than 50% of frames are below silence threshold",
        "#   FLATPITCH — F0 std is unusually low (possible quantization/artifact)",
        "#",
    ]
    current_song = None
    for a in audits:
        if a["song"] != current_song:
            current_song = a["song"]
            lines.append(f"")
            lines.append(f"# ── song: {current_song} ──")
        flag_text = f" [{a['flag']}]" if a["flag"] else ""
        lines.append(
            f"keep {a['name']}  "
            f"# dur={a['duration']:.1f}s  sil={a['silence_pct']:.0f}%  "
            f"f0std={a['f0_std']:.0f}{flag_text}"
        )

    manifest_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nManifest written: {manifest_path}")
    print(f"Total files: {len(wavs)}")
    print(f"Flagged for prioritized review: {flagged}")
    print()
    print("Workflow:")
    print(f"  1. Open {manifest_path} in your editor")
    print("  2. Listen to each wav, mark unwanted as 'drop' (replace 'keep')")
    print(f"     Flagged segments (SHORT/SILENT/FLATPITCH) are likely-drop candidates — review first")
    print(f"  3. Apply: python scripts/singer_pipeline.py curate {singer} --apply")


# ── CLI ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    pm = sub.add_parser("measure", help="similarity vs Koroki centroid")
    pm.add_argument("singer", help="singer name (subdir under data/diffsinger_raw/)")
    pm.add_argument("--sample-size", type=int, default=8)
    pm.add_argument("--threshold", type=float, default=0.65)
    pm.set_defaults(func=cmd_measure)

    pt = sub.add_parser("test", help="small batch RVC at multiple index_rates")
    pt.add_argument("singer")
    pt.add_argument("--segments", type=int, default=3)
    pt.add_argument("--index-rates", type=str, default="0.4,0.6,0.75,0.9",
                     help="comma-separated index_rate values to sweep")
    pt.add_argument("--model", type=str, default="Korokiv2")
    pt.add_argument("--transpose", type=int, default=0)
    pt.add_argument("--protect", type=float, default=0.33)
    pt.set_defaults(func=cmd_test)

    pc = sub.add_parser("convert", help="full batch RVC at chosen index_rate")
    pc.add_argument("singer")
    pc.add_argument("--index-rate", type=float, required=True,
                     help="RVC index_rate (higher = more Koroki identity)")
    pc.add_argument("--model", type=str, default="Korokiv2")
    pc.add_argument("--transpose", type=int, default=0)
    pc.add_argument("--protect", type=float, default=0.33)
    pc.add_argument("--limit", type=int, default=0)
    pc.set_defaults(func=cmd_convert)

    pcu = sub.add_parser("curate", help="generate / apply curation manifest")
    pcu.add_argument("singer")
    pcu.add_argument("--apply", action="store_true",
                      help="apply the manifest (delete files marked 'drop')")
    pcu.set_defaults(func=cmd_curate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
