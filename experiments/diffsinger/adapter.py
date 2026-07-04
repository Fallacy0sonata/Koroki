"""
DiffSinger singing adapter for Koroki Ascension 2.0.

Wraps sing_song.py as an HTTP service. sing_song.py caches all pipeline
stages by song slug — repeat requests for the same song return instantly
from cache without re-downloading or re-synthesizing.

Port:  9003
Venv:  main .venv (Python 3.12, has FastAPI/uvicorn)
       sing_song.py is invoked as subprocess under .venv_diffsinger

Endpoints:
  GET  /health   — liveness
  GET  /ready    — 503 until config validated
  GET  /version  — service version + active model info
  POST /load     — validate checkpoint exists (no persistent GPU state)
  POST /unload   — mark not-ready (no GPU to free)
  POST /sing     — full pipeline, returns { wav_base64, title }

Config (config/settings.yaml, section singing_diffsinger):
  exp:  DiffSinger experiment name (checkpoint directory name)
  ckpt: checkpoint step number (integer)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("diffsinger.adapter")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(title="Koroki DiffSinger Adapter", version="1.0.0")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SING_SONG_PY = Path(__file__).parent / "sing_song.py"

# Populated at startup / /load
_ready: bool = False
_diffsinger_python: str = str(_REPO_ROOT / ".venv_diffsinger" / "Scripts" / "python.exe")
_diffsinger_exp: str = "koroki_v6"
_diffsinger_ckpt: int = 160000

# Serialize requests — sing_song.py caches by slug with no concurrent-access safety
_sing_lock: asyncio.Lock | None = None


class SingRequest(BaseModel):
    request_id: str = Field(default="", max_length=128)
    song: str = Field(..., min_length=1, max_length=256)
    transpose: Annotated[int, Field(ge=-24, le=24)] = 0  # reserved — DiffSinger uses auto key-shift
    vocal_gain: float = Field(default=1.4, ge=0.1, le=3.0)


class LoadRequest(BaseModel):
    diffsinger_exp: str | None = None
    diffsinger_ckpt: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ckpt_path(exp: str, ckpt: int) -> Path:
    return (
        _REPO_ROOT
        / "experiments" / "diffsinger" / "DiffSinger"
        / "checkpoints" / exp
        / f"model_ckpt_steps_{ckpt}.ckpt"
    )


def _validate_config(exp: str, ckpt: int) -> str | None:
    """Return error string if config is invalid, else None."""
    if not Path(_diffsinger_python).exists():
        return f".venv_diffsinger python not found: {_diffsinger_python}"
    if not _SING_SONG_PY.exists():
        return f"sing_song.py not found: {_SING_SONG_PY}"
    if not _ckpt_path(exp, ckpt).exists():
        return f"Checkpoint not found: {_ckpt_path(exp, ckpt)}"
    return None


def _run_pipeline(song: str, vocal_gain: float, label: str) -> str:
    """Blocking: run sing_song.py subprocess, return path to output WAV."""
    cmd = [
        _diffsinger_python,
        str(_SING_SONG_PY),
        song,
        "--diffsinger-exp", _diffsinger_exp,
        "--diffsinger-ckpt", str(_diffsinger_ckpt),
        "--vocal-gain", str(vocal_gain),
    ]
    logger.info("[%s] Running pipeline: %s", label, " ".join(cmd[:6]))
    result = subprocess.run(cmd, cwd=str(_REPO_ROOT), timeout=900)
    if result.returncode != 0:
        raise RuntimeError(f"sing_song.py failed (rc={result.returncode})")

    slug = re.sub(r"[^\w\-]", "_", song)[:50].strip("_")
    output_wav = _REPO_ROOT / "data" / "diffsinger_output" / f"{slug}.wav"
    if not output_wav.exists():
        raise FileNotFoundError(f"Output wav not found: {output_wav}")
    return str(output_wav)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "diffsinger",
        "version": "1.0.0",
        "ready": _ready,
    }


@app.get("/ready")
async def ready():
    if not _ready:
        raise HTTPException(status_code=503, detail="DiffSinger not configured")
    return {"status": "ready"}


@app.get("/version")
async def version_info():
    return {
        "service": "diffsinger",
        "version": "1.0.0",
        "exp": _diffsinger_exp,
        "ckpt": _diffsinger_ckpt,
    }


@app.post("/load")
async def load_model(req: LoadRequest | None = None):
    global _ready, _diffsinger_exp, _diffsinger_ckpt

    exp  = (req.diffsinger_exp  if req and req.diffsinger_exp  else None) or _diffsinger_exp
    ckpt = (req.diffsinger_ckpt if req and req.diffsinger_ckpt else None) or _diffsinger_ckpt

    err = _validate_config(exp, ckpt)
    if err:
        raise HTTPException(status_code=500, detail=err)

    _diffsinger_exp  = exp
    _diffsinger_ckpt = ckpt
    _ready = True
    logger.info("DiffSinger ready: exp=%s ckpt=%d", _diffsinger_exp, _diffsinger_ckpt)
    return {"status": "ready", "exp": _diffsinger_exp, "ckpt": _diffsinger_ckpt}


@app.post("/unload")
async def unload_model():
    global _ready
    _ready = False
    logger.info("DiffSinger unloaded")
    return {"status": "unloaded"}


@app.post("/sing")
async def sing(req: SingRequest):
    if not _ready:
        raise HTTPException(status_code=503, detail="DiffSinger not configured — call /load first")

    label = req.request_id or "sing"
    logger.info("[%s] Singing request: song='%s'", label, req.song)

    assert _sing_lock is not None
    async with _sing_lock:
        try:
            result_wav = await asyncio.to_thread(
                _run_pipeline, req.song, req.vocal_gain, label
            )
        except Exception as exc:
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(exc))

    wav_bytes = Path(result_wav).read_bytes()
    logger.info("[%s] Done — %d bytes", label, len(wav_bytes))
    return {
        "wav_base64": base64.b64encode(wav_bytes).decode("ascii"),
        "title": req.song,
    }


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    global _sing_lock, _diffsinger_python, _diffsinger_exp, _diffsinger_ckpt, _ready

    _sing_lock = asyncio.Lock()

    koroki_root = Path(os.environ.get("KOROKI_ROOT", str(_REPO_ROOT)))
    settings_path = koroki_root / "config" / "settings.yaml"

    if settings_path.exists():
        try:
            import yaml
            with open(settings_path) as f:
                cfg = yaml.safe_load(f)
            ds_cfg = cfg.get("singing_diffsinger", {})
            if ds_cfg.get("exp"):
                _diffsinger_exp = ds_cfg["exp"]
            if ds_cfg.get("ckpt"):
                _diffsinger_ckpt = int(ds_cfg["ckpt"])
        except Exception as exc:
            logger.warning("Could not read settings.yaml: %s", exc)

    err = _validate_config(_diffsinger_exp, _diffsinger_ckpt)
    if err:
        logger.warning("DiffSinger not auto-ready: %s", err)
    else:
        _ready = True
        logger.info("DiffSinger auto-configured: exp=%s ckpt=%d", _diffsinger_exp, _diffsinger_ckpt)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Koroki DiffSinger singing adapter")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9003)
    return parser.parse_args()


if __name__ == "__main__":
    import uvicorn
    args = _parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
