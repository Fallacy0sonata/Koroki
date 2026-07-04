# Koroki Singing Adapter — startup script
# Runs the RVC singing pipeline on port 9001 using the .venv_singing venv.
#
# First-time setup:
#   python -m venv .venv_singing
#   .venv_singing\Scripts\pip install -r experiments\singing\requirements.txt
#
# Then place trained RVC model files at:
#   adapters\singing\koroki.pth   (required)
#   adapters\singing\koroki.index (optional, improves quality)
#
# To enable singing in the orchestrator, set singing.enabled: true in config\settings.yaml

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

$VenvPython = Join-Path $RepoRoot ".venv_singing\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "ERROR: .venv_singing not found."
    Write-Host "Run: python -m venv .venv_singing"
    Write-Host "Then: .venv_singing\Scripts\pip install -r experiments\singing\requirements.txt"
    exit 1
}

$env:KOROKI_ROOT = $RepoRoot
$env:PYTHONPATH = $RepoRoot

$AdapterScript = Join-Path $RepoRoot "experiments\singing\adapter.py"
Write-Host "Starting Koroki Singing Adapter (port 9001)..."
& $VenvPython $AdapterScript --host 0.0.0.0 --port 9001
