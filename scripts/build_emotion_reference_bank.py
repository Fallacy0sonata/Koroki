"""Build the RVC emotional reference bank (research arc item 2, approved 2026-07-06).

Chain: RAVDESS acted emotional speech (free, real humans, strong prosody)
  -> concat 4 clips per emotion per actor (~14s reference length)
  -> Korokiv5 RVC (Applio, same proven recipe as convert_yoasobi_rvc.py)
  -> voice_samples/emotion_bank/<emotion>__actorNN.wav  (Koroki-voiced, emotion intact)

The bank feeds CosyVoice prompt-swap in cross_lingual mode (instruct2 strips
reference emotion to timbre-only — LEGACY 2026-07-06) and later IndexTTS on the
3090. Breathiness/tension/laugh-edge survive RVC; timbre becomes hers — the same
trick as the singing chain, applied to speech.

Usage (from Koroki root, main .venv):
    .venv\\Scripts\\python.exe scripts\\build_emotion_reference_bank.py --ravdess <extracted_dir>
    # add --dry-run to preview clip selection without converting
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

_REPO_ROOT = Path(__file__).resolve().parents[1]
_APPLIO_DIR = _REPO_ROOT / "ApplioV3.6.2"
_APPLIO_PY = _APPLIO_DIR / "env" / "python.exe"
_APPLIO_CORE = _APPLIO_DIR / "core.py"
_KOROKIV5_PTH = _REPO_ROOT / "adapters" / "singing" / "Korokiv5_300e_34500s_best_epoch.pth"
_KOROKIV5_INDEX = _REPO_ROOT / "adapters" / "singing" / "Korokiv5.index"
_OUT_DIR = _REPO_ROOT / "voice_samples" / "emotion_bank"

# RAVDESS filename: Modality-Channel-Emotion-Intensity-Statement-Repetition-Actor.wav
_EMOTIONS = {"01": "neutral", "02": "calm", "03": "happy", "04": "sad",
             "05": "angry", "06": "fearful", "07": "disgust", "08": "surprised"}
# Even-numbered actors are female — closest register to her voice for clean RVC.
_ACTORS = ["08", "12", "16"]
_GAP_S = 0.25


def _collect_clips(ravdess: Path) -> dict[tuple[str, str], list[Path]]:
    """(emotion, actor) -> clips. Strong intensity (02) preferred; neutral only has 01."""
    groups: dict[tuple[str, str], list[Path]] = {}
    for wav in sorted(ravdess.rglob("03-01-*.wav")):
        parts = wav.stem.split("-")
        if len(parts) != 7:
            continue
        _, _, emo, intensity, _, _, actor = parts
        if actor not in _ACTORS or emo not in _EMOTIONS:
            continue
        want = "01" if emo == "01" else "02"
        if intensity != want:
            continue
        groups.setdefault((_EMOTIONS[emo], actor), []).append(wav)
    return groups


def _concat(clips: list[Path], out: Path) -> None:
    pieces, sr = [], None
    for c in clips:
        data, this_sr = sf.read(c, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        # trim leading/trailing silence so the reference is dense speech
        nz = np.where(np.abs(data) > 0.005)[0]
        if len(nz):
            data = data[max(0, nz[0] - 2400): nz[-1] + 2400]
        sr = sr or this_sr
        pieces.append(data)
        pieces.append(np.zeros(int(_GAP_S * sr), dtype=np.float32))
    sf.write(out, np.concatenate(pieces[:-1]), sr)


def _rvc_convert(wav_in: Path, wav_out: Path) -> bool:
    """Korokiv5 through Applio — flags copied from the proven singing chain."""
    with tempfile.NamedTemporaryFile(suffix=".wav", dir=_APPLIO_DIR, delete=False) as tmp:
        tmp_out = Path(tmp.name)
    cmd = [
        str(_APPLIO_PY), str(_APPLIO_CORE), "infer",
        "--input_path", str(wav_in),
        "--output_path", str(tmp_out),
        "--pth_path", str(_KOROKIV5_PTH),
        "--index_path", str(_KOROKIV5_INDEX),
        "--pitch", "0",
        "--f0_method", "rmvpe",
        "--index_rate", "0.75",
        "--volume_envelope", "1.0",
        "--protect", "0.33",
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
    result = subprocess.run(cmd, cwd=str(_APPLIO_DIR), capture_output=True, text=True,
                            timeout=600)
    if result.returncode != 0:
        for line in (result.stderr or "").splitlines()[-5:]:
            print(f"    [applio:err] {line}")
        tmp_out.unlink(missing_ok=True)
        return False
    if tmp_out.exists() and tmp_out.stat().st_size > 0:
        wav_out.parent.mkdir(parents=True, exist_ok=True)
        tmp_out.replace(wav_out)
        return True
    tmp_out.unlink(missing_ok=True)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ravdess", required=True, help="extracted RAVDESS speech dir")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    groups = _collect_clips(Path(args.ravdess))
    if not groups:
        raise SystemExit("no RAVDESS clips matched — check the directory")
    print(f"{len(groups)} (emotion, actor) groups "
          f"({len(set(k[0] for k in groups))} emotions x {_ACTORS} actors)")

    manifest = {}
    work = _REPO_ROOT / "data" / "tmp_emotion_bank_src"
    work.mkdir(parents=True, exist_ok=True)
    for (emotion, actor), clips in sorted(groups.items()):
        name = f"{emotion}__actor{actor}"
        out = _OUT_DIR / f"{name}.wav"
        if out.exists() and out.stat().st_size > 0:
            print(f"  [skip] {name} (exists)")
            manifest[name] = {"emotion": emotion, "actor": actor, "clips": len(clips)}
            continue
        if args.dry_run:
            print(f"  [dry-run] {name}: {len(clips)} clips -> {out}")
            continue
        src = work / f"{name}_src.wav"
        _concat(clips[:4], src)
        t0 = time.perf_counter()
        ok = _rvc_convert(src, out)
        print(f"  [{'ok' if ok else 'FAIL'}] {name} "
              f"({len(clips[:4])} clips, {time.perf_counter()-t0:.0f}s)")
        if ok:
            manifest[name] = {"emotion": emotion, "actor": actor, "clips": len(clips[:4])}
    if not args.dry_run:
        (_OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print(f"\nbank: {_OUT_DIR} ({len(manifest)} references + manifest.json)")
        print("cleanup: data/tmp_emotion_bank_src/ can be deleted after ear-check")


if __name__ == "__main__":
    main()
