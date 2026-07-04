# Starts the CosyVoice adapter on port 9004.
# Run this in a separate terminal before using gen_koroki_singing_data.py with --tts-url http://127.0.0.1:9004

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $PSScriptRoot

$VENV = "$ROOT\.venv_cosyvoice"
$PYTHON = "$VENV\Scripts\python.exe"
$ADAPTER = "$ROOT\experiments\cosyvoice\adapter.py"

if (-not (Test-Path $PYTHON)) {
    Write-Error "CosyVoice venv not found at $VENV. Run scripts\setup_cosyvoice.ps1 first."
    exit 1
}

if (-not (Test-Path "$ROOT\experiments\cosyvoice\pretrained_models\CosyVoice2-0.5B")) {
    Write-Warning "Model checkpoint not found. Run scripts\setup_cosyvoice.ps1 first."
}

Write-Host "Starting CosyVoice adapter on port 9004..."
Set-Location $ROOT
& $PYTHON $ADAPTER --port 9004 --host 127.0.0.1
