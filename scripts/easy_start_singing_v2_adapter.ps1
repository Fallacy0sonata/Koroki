# Koroki Singing v2 Adapter — Seed-VC pipeline, port 9002
#
# First-time setup:
#   1. py -3.11 -m venv .venv_singing_v2
#   2. .venv_singing_v2\Scripts\pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
#   3. .venv_singing_v2\Scripts\pip install -r experiments\singing-v2\requirements.txt
#   4. git clone https://github.com/Plachtaa/seed-vc experiments\singing-v2\seed-vc
#   5. .venv_singing_v2\Scripts\pip install -r experiments\singing-v2\seed-vc\requirements.txt
#
# To switch orchestrator to use this adapter instead of v1:
#   Set singing.adapter_url: http://127.0.0.1:9002 in config\settings.yaml

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

$VenvPython = Join-Path $RepoRoot ".venv_singing_v2\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "ERROR: .venv_singing_v2 not found." -ForegroundColor Red
    Write-Host ""
    Write-Host "Run these commands to set up:" -ForegroundColor Yellow
    Write-Host "  py -3.11 -m venv .venv_singing_v2"
    Write-Host "  .venv_singing_v2\Scripts\pip install -r experiments\singing-v2\requirements.txt"
    Write-Host "  git clone https://github.com/Plachtaa/seed-vc experiments\singing-v2\seed-vc"
    Write-Host "  .venv_singing_v2\Scripts\pip install -r experiments\singing-v2\seed-vc\requirements.txt"
    exit 1
}

$SeedVcDir = Join-Path $RepoRoot "experiments\singing-v2\seed-vc"
if (-not (Test-Path $SeedVcDir)) {
    Write-Host "ERROR: Seed-VC not cloned." -ForegroundColor Red
    Write-Host "Run: git clone https://github.com/Plachtaa/seed-vc $SeedVcDir" -ForegroundColor Yellow
    exit 1
}

$env:KOROKI_ROOT = $RepoRoot
$env:PYTHONPATH = $RepoRoot

$AdapterScript = Join-Path $RepoRoot "experiments\singing-v2\adapter.py"
Write-Host "Starting Koroki Singing v2 Adapter (Seed-VC, port 9002)..." -ForegroundColor Cyan
& $VenvPython $AdapterScript --host 0.0.0.0 --port 9002
