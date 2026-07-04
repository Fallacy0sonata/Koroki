import logging
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pathlib import Path
from shared.utils.config import get_settings

router = APIRouter()
logger = logging.getLogger("orchestrator.logs")

# Base directories for logs
_REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_BASE_DIR = _REPO_ROOT / "data" / "logs"
TRAIN_LOG_DIR = _REPO_ROOT / "logs"

@router.get("/logs", response_class=PlainTextResponse)
async def get_logs(
    service: str = Query(None, description="Service name: orchestrator, brain, tts, discord, web"),
    filename: str = Query(None, description="Specific log filename (without .log)"),
    lines: int = Query(200, description="Number of lines to return from end of file", ge=1, le=5000)
):
    """
    Retrieve logs from various services.
    Looks in data/logs/ first, then falls back to logs/ for training logs.
    Returns the last N lines of the log file as plain text.
    """
    try:
        # Determine which log file to read
        if filename:
            candidates = [
                LOG_BASE_DIR / f"{filename}.log",
                TRAIN_LOG_DIR / f"{filename}.log",
            ]
        elif service:
            candidates = [
                LOG_BASE_DIR / f"{service}.log",
                TRAIN_LOG_DIR / f"train_{service}.log",
            ]
        else:
            candidates = [
                LOG_BASE_DIR / "orchestrator.log",
            ]

        log_file = None
        for candidate in candidates:
            candidate = candidate.resolve()
            # Security: ensure within allowed dirs
            try:
                candidate.relative_to(LOG_BASE_DIR.resolve())
                log_file = candidate
                break
            except ValueError:
                pass
            try:
                candidate.relative_to(TRAIN_LOG_DIR.resolve())
                log_file = candidate
                break
            except ValueError:
                pass

        if log_file is None or not log_file.exists():
            available = []
            for d in [LOG_BASE_DIR, TRAIN_LOG_DIR]:
                if d.exists():
                    for f in d.glob("*.log"):
                        available.append(f.stem)
            return PlainTextResponse(
                content=f"(no log file found for '{service or filename}')\n\nAvailable: {', '.join(available) or '(none)'}"
            )

        # Read the last N lines
        with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
            all_lines = f.readlines()
            last_lines = all_lines[-lines:] if len(all_lines) >= lines else all_lines
            return PlainTextResponse(content="".join(last_lines))

    except Exception as e:
        logger.error(f"Error retrieving logs: {e}")
        return PlainTextResponse(content=f"(error reading logs: {e})")


@router.get("/logs/list")
async def list_available_logs():
    """
    List all available log files in the logs directories.
    """
    try:
        log_files = []
        for directory in [LOG_BASE_DIR, TRAIN_LOG_DIR]:
            if directory.exists():
                for file in directory.glob("*.log"):
                    log_files.append({
                        "name": file.stem,
                        "dir": str(directory.name),
                        "size_bytes": file.stat().st_size,
                        "modified": file.stat().st_mtime
                    })
        return {"logs": log_files}
    except Exception as e:
        logger.error(f"Error listing log files: {e}")
        return {"logs": [], "error": str(e)}