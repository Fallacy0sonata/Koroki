"""
Singing adapter for Koroki Ascension 2.0.

Pipeline per request:
  song name → yt-dlp (download) → demucs (vocal separation)
            → RVC (voice conversion) → mix vocals + instrumental → wav_base64

Runs in its own Python 3.11 venv (.venv_singing) on port 9001.
IndexTTS must be unloaded before calling /load here (VRAM constraint).

Endpoints:
  GET  /health    — liveness
  GET  /ready     — 503 if RVC model not loaded
  GET  /version   — service version
  POST /load      — load RVC model into VRAM
  POST /unload    — free RVC model from VRAM
  POST /sing      — full pipeline, returns { wav_base64, title }

Setup (one-time):
  1. Install deps: pip install rvc-python demucs yt-dlp soundfile numpy
  2. Train or download an RVC model for Koroki's voice (.pth + .index)
  3. Place model files at the paths in settings.yaml (adapters/singing/)
  4. Run: python experiments/singing/adapter.py
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import shutil
import subprocess
import tempfile
import traceback
from pathlib import Path
from typing import Annotated

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("singing.adapter")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

app = FastAPI(title="Koroki Singing Adapter", version="1.0.0")

_REPO_ROOT = Path(__file__).resolve().parents[2]

# RVC via Applio subprocess (avoids fairseq/Python 3.11 incompatibility)
_loaded: bool = False
_rvc_model_path: str | None = None
_rvc_index_path: str | None = None
_applio_python: str | None = None
_applio_core: str | None = None

# yt-dlp path: prefer the venv's own executable over PATH
import sys as _sys
_YTDLP = str(Path(_sys.executable).parent / "yt-dlp.exe")
if not Path(_YTDLP).exists():
    _YTDLP = str(Path(_sys.executable).parent / "yt-dlp")
if not Path(_YTDLP).exists():
    _YTDLP = "yt-dlp"


class SingRequest(BaseModel):
    request_id: str = Field(default="", max_length=128)
    song: str = Field(..., min_length=1, max_length=256)
    transpose: Annotated[int, Field(ge=-24, le=24)] = 0
    vocal_gain: float = Field(default=1.0, ge=0.1, le=3.0)


class LoadRequest(BaseModel):
    model_path: str | None = None
    index_path: str | None = None


# ---------------------------------------------------------------------------
# Pipeline steps (all blocking — called via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _download_song(song_query: str, work_dir: str) -> str:
    """Download song audio via yt-dlp, returns path to downloaded WAV."""
    out_template = os.path.join(work_dir, "source.%(ext)s")
    cmd = [
        _YTDLP,
        "--no-playlist",
        "--extract-audio",
        "--audio-format", "wav",
        "--audio-quality", "0",
        "--output", out_template,
        "--default-search", "ytsearch",
        "--no-warnings",
        f"ytsearch1:{song_query}",
    ]
    logger.info("yt-dlp: searching and downloading '%s'", song_query)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr[:500]}")

    wav_path = os.path.join(work_dir, "source.wav")
    if os.path.exists(wav_path):
        return wav_path

    # yt-dlp may name the file differently — find it
    for p in Path(work_dir).glob("source.*"):
        logger.info("Found downloaded file: %s", p)
        return str(p)

    raise FileNotFoundError("yt-dlp produced no output file")


def _separate_vocals(source_path: str, work_dir: str) -> tuple[str, str]:
    """Run demucs htdemucs (two-stem) to get vocals + no_vocals.
    Returns (vocals_path, no_vocals_path).
    """
    logger.info("demucs: separating vocals from '%s'", source_path)
    stem_name = Path(source_path).stem
    try:
        import demucs.api
        separator = demucs.api.Separator(
            model="htdemucs",
            device=_rvc_device if _rvc_device else "cuda",
            jobs=1,
            two_stems="vocals",
            progress=False,
        )
        origin, separated = separator.separate_audio_file(Path(source_path))
        stems_dir = Path(work_dir) / "stems"
        stems_dir.mkdir(exist_ok=True)
        vocals_path = str(stems_dir / "vocals.wav")
        no_vocals_path = str(stems_dir / "no_vocals.wav")
        separator.save_audio(separated["vocals"], Path(vocals_path), samplerate=separator.samplerate)
        separator.save_audio(separated["no_vocals"], Path(no_vocals_path), samplerate=separator.samplerate)
    except Exception:
        # Fallback: call demucs via subprocess
        logger.warning("demucs API import failed, falling back to subprocess")
        out_dir = os.path.join(work_dir, "demucs_out")
        cmd = [
            _sys.executable, "-m", "demucs",
            "--name", "htdemucs",
            "--two-stems", "vocals",
            "--out", out_dir,
            source_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"demucs failed (rc={result.returncode}):\nSTDOUT: {result.stdout[-1000:]}\nSTDERR: {result.stderr[-1000:]}")
        base = Path(out_dir) / "htdemucs" / stem_name
        vocals_path = str(base / "vocals.wav")
        no_vocals_path = str(base / "no_vocals.wav")

    return vocals_path, no_vocals_path


def _run_rvc(vocals_path: str, output_path: str, transpose: int) -> None:
    """Convert vocals to Koroki's voice using Applio's RVC via subprocess."""
    if not _loaded:
        raise RuntimeError("RVC not configured — call /load first")
    if not _applio_python or not _applio_core:
        raise RuntimeError("Applio path not configured")
    logger.info("RVC (Applio): converting vocals (transpose=%d semitones)", transpose)
    cmd = [
        _applio_python, _applio_core, "infer",
        "--input_path", vocals_path,
        "--output_path", output_path,
        "--pth_path", _rvc_model_path,
        "--index_path", _rvc_index_path or "",
        "--pitch", str(transpose),
        "--f0_method", "rmvpe",
        "--index_rate", "0.4",
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
    applio_dir = str(Path(_applio_core).parent)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=applio_dir)
    if result.returncode != 0:
        raise RuntimeError(f"Applio RVC failed:\n{result.stderr[:1000]}")
    if not os.path.exists(output_path):
        # Applio may save as .wav regardless of extension — scan for it
        parent = Path(output_path).parent
        candidates = list(parent.glob("*.wav"))
        if not candidates:
            raise FileNotFoundError("Applio RVC produced no output file")
        import shutil as _sh
        _sh.copy(str(candidates[0]), output_path)


def _resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    if orig_sr == target_sr:
        return audio
    try:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(orig_sr, target_sr)
        up, down = target_sr // g, orig_sr // g
        if audio.ndim == 1:
            return resample_poly(audio, up, down).astype(audio.dtype)
        return np.stack(
            [resample_poly(audio[:, c], up, down) for c in range(audio.shape[1])],
            axis=1,
        ).astype(audio.dtype)
    except ImportError:
        pass
    try:
        import resampy
        if audio.ndim == 1:
            return resampy.resample(audio, orig_sr, target_sr).astype(audio.dtype)
        return resampy.resample(audio.T, orig_sr, target_sr).T.astype(audio.dtype)
    except ImportError:
        pass
    # last resort: ffmpeg subprocess
    import tempfile, subprocess as _sp
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_in:
        sf.write(tmp_in.name, audio, orig_sr)
        tmp_out = tmp_in.name.replace(".wav", "_rs.wav")
        _sp.run(["ffmpeg", "-y", "-i", tmp_in.name, "-ar", str(target_sr), tmp_out],
                capture_output=True, check=True)
        out, _ = sf.read(tmp_out)
        os.unlink(tmp_in.name)
        os.unlink(tmp_out)
        return out.astype(audio.dtype)


def _mix_audio(rvc_vocals_path: str, no_vocals_path: str, output_path: str, vocal_gain: float) -> None:
    """Mix RVC-converted vocals with the original instrumental track."""
    vocals, vsr = sf.read(rvc_vocals_path)
    instrumental, isr = sf.read(no_vocals_path)

    logger.info("Mixing: vocals %dHz %s, instrumental %dHz %s",
                vsr, vocals.shape, isr, instrumental.shape)

    # Mono → stereo
    if vocals.ndim == 1:
        vocals = np.stack([vocals, vocals], axis=-1)
    if instrumental.ndim == 1:
        instrumental = np.stack([instrumental, instrumental], axis=-1)

    # Resample RVC vocals to instrumental SR (instrumental is the reference — same as original song)
    if vsr != isr:
        logger.warning("Sample rate mismatch: resampling vocals %dHz → %dHz", vsr, isr)
        vocals = _resample(vocals, vsr, isr)

    # Align lengths (trim longer track)
    min_len = min(len(vocals), len(instrumental))
    vocals = vocals[:min_len]
    instrumental = instrumental[:min_len]

    mixed = vocal_gain * vocals + instrumental
    peak = np.max(np.abs(mixed))
    if peak > 1.0:
        mixed = mixed / peak

    sf.write(output_path, mixed, isr, subtype="PCM_16")
    logger.info("Mixed audio: %d samples @ %d Hz", len(mixed), isr)


def _full_pipeline(song: str, transpose: int, vocal_gain: float, work_dir: str) -> str:
    """Blocking: runs the full download → separate → convert → mix pipeline.
    Returns path to final mixed WAV.
    """
    source = _download_song(song, work_dir)
    vocals_raw, no_vocals = _separate_vocals(source, work_dir)
    rvc_out = os.path.join(work_dir, "rvc_vocals.wav")
    _run_rvc(vocals_raw, rvc_out, transpose)
    final_out = os.path.join(work_dir, "final.wav")
    _mix_audio(rvc_out, no_vocals, final_out, vocal_gain)
    return final_out


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "singing", "version": "1.0.0", "ready": _loaded}


@app.get("/ready")
async def ready():
    if not _loaded:
        raise HTTPException(status_code=503, detail="RVC not configured")
    return {"status": "ready"}


@app.get("/version")
async def version_info():
    return {"service": "singing", "version": "1.0.0"}


@app.post("/load")
async def load_model(req: LoadRequest | None = None):
    """Validate Applio + model paths are reachable. No GPU load — RVC runs per-request via subprocess."""
    global _loaded, _rvc_model_path, _rvc_index_path

    if _loaded:
        return {"status": "already_loaded"}

    model_path = (req.model_path if req else None) or _rvc_model_path
    index_path = (req.index_path if req else None) or _rvc_index_path

    if not model_path:
        raise HTTPException(status_code=400, detail="No RVC model path configured")
    if not os.path.exists(model_path):
        raise HTTPException(status_code=404, detail=f"RVC model not found: {model_path}")
    if not _applio_python or not os.path.exists(_applio_python):
        raise HTTPException(status_code=500, detail=f"Applio python not found: {_applio_python}")

    _rvc_model_path = model_path
    _rvc_index_path = index_path
    _loaded = True
    logger.info("RVC configured: model=%s index=%s applio=%s", model_path, index_path, _applio_python)
    return {"status": "loaded"}


@app.post("/unload")
async def unload_model():
    """Mark RVC as unloaded (no persistent GPU state to free — subprocess-based)."""
    global _loaded
    if not _loaded:
        return {"status": "already_unloaded"}
    _loaded = False
    logger.info("RVC unloaded")
    return {"status": "unloaded"}


@app.post("/sing")
async def sing(req: SingRequest):
    """Run full singing pipeline: download → separate → convert → mix → return wav."""
    if not _loaded:
        raise HTTPException(status_code=503, detail="RVC not configured — call /load first")

    work_dir = tempfile.mkdtemp(prefix="koroki_sing_")
    try:
        label = req.request_id or "sing"
        logger.info("[%s] Singing request: song='%s' transpose=%d", label, req.song, req.transpose)

        final_path = await asyncio.to_thread(
            _full_pipeline,
            req.song,
            req.transpose,
            req.vocal_gain,
            work_dir,
        )

        wav_bytes = Path(final_path).read_bytes()
        logger.info("[%s] Singing done — %d bytes", label, len(wav_bytes))
        return {
            "wav_base64": base64.b64encode(wav_bytes).decode("ascii"),
            "title": req.song,
        }
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Startup: read config from environment / settings.yaml
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    global _rvc_model_path, _rvc_index_path, _applio_python, _applio_core

    koroki_root = Path(os.environ.get("KOROKI_ROOT", str(_REPO_ROOT)))
    settings_path = koroki_root / "config" / "settings.yaml"

    model_path: str | None = os.environ.get("SINGING_RVC_MODEL")
    index_path: str | None = os.environ.get("SINGING_RVC_INDEX")
    applio_dir_override: str | None = os.environ.get("SINGING_APPLIO_DIR")

    if settings_path.exists():
        try:
            import yaml
            with open(settings_path) as f:
                cfg = yaml.safe_load(f)
            singing_cfg = cfg.get("singing", {})
            model_path = model_path or str(koroki_root / singing_cfg.get("rvc_model_path", "adapters/singing/koroki.pth"))
            index_path = index_path or str(koroki_root / singing_cfg.get("rvc_index_path", "adapters/singing/koroki.index"))
            applio_rel = singing_cfg.get("applio_dir", "ApplioV3.6.2")
            applio_dir_override = applio_dir_override or str(koroki_root / applio_rel)
        except Exception as exc:
            logger.warning("Could not read settings.yaml: %s", exc)

    _rvc_model_path = model_path
    _rvc_index_path = index_path if index_path and os.path.exists(index_path) else None

    if applio_dir_override:
        applio_dir = Path(applio_dir_override)
        _applio_python = str(applio_dir / "env" / "python.exe")
        _applio_core = str(applio_dir / "core.py")
        if os.path.exists(_applio_python) and os.path.exists(_applio_core):
            logger.info("Applio found: %s", applio_dir)
        else:
            logger.warning("Applio not found at '%s' — check singing.applio_dir in settings.yaml", applio_dir)

    if _rvc_model_path and os.path.exists(_rvc_model_path):
        logger.info("RVC model found: %s — call /load to activate", _rvc_model_path)
    else:
        logger.warning("RVC model not found at '%s'", _rvc_model_path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Koroki singing adapter (RVC pipeline)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9001)
    return parser.parse_args()


if __name__ == "__main__":
    import uvicorn
    args = _parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
