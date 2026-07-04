# Koroki DiffSinger Singing Adapter — port 9003
#
# Runs in the main .venv (Python 3.12 — has FastAPI/uvicorn).
# sing_song.py is invoked as a subprocess under .venv_diffsinger.
#
# To switch orchestrator to use DiffSinger instead of Seed-VC/RVC:
#   Set singing.adapter_url: http://127.0.0.1:9003 in config\settings.yaml
#   (already set if you ran the setup)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "ERROR: main .venv not found at $VenvPython" -ForegroundColor Red
    exit 1
}

$DiffsingerPython = Join-Path $RepoRoot ".venv_diffsinger\Scripts\python.exe"
if (-not (Test-Path $DiffsingerPython)) {
    Write-Host "ERROR: .venv_diffsinger not found." -ForegroundColor Red
    Write-Host "The DiffSinger training venv must exist for sing_song.py to run." -ForegroundColor Yellow
    exit 1
}

$AdapterScript = Join-Path $RepoRoot "experiments\diffsinger\adapter.py"
if (-not (Test-Path $AdapterScript)) {
    Write-Host "ERROR: adapter.py not found at $AdapterScript" -ForegroundColor Red
    exit 1
}

$env:KOROKI_ROOT = $RepoRoot
$env:PYTHONPATH  = $RepoRoot

Write-Host "Starting Koroki DiffSinger Adapter (port 9003)..." -ForegroundColor Cyan
Write-Host "  Adapter venv : $VenvPython" -ForegroundColor Gray
Write-Host "  DiffSinger   : $DiffsingerPython" -ForegroundColor Gray
Write-Host ""

& $VenvPython $AdapterScript --host 0.0.0.0 --port 9003
