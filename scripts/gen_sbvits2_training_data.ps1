# Generates 1.5 hours of clean Koroki English voice clips via IndexTTS.
# Output goes to data\sbvits2_training\ for Style-Bert-VITS2 training.
# IndexTTS must be running (easy_start_tts_adapter.ps1) before running this.

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $PSScriptRoot

$PYTHON = "$ROOT\.venv_indextts\Scripts\python.exe"
$SCRIPT = "$ROOT\experiments\style-bert-vits2\gen_indexTTS_training_data.py"

if (-not (Test-Path $PYTHON)) {
    Write-Error "IndexTTS venv not found at .venv_indextts"
    exit 1
}

Write-Host "Generating IndexTTS training data for Style-Bert-VITS2..." -ForegroundColor Cyan
Write-Host "Target: 1.5 hours. IndexTTS must be running on port 9000." -ForegroundColor DarkGray
Write-Host "Output: $ROOT\data\sbvits2_training\" -ForegroundColor DarkGray
Write-Host ""

Set-Location $ROOT
& $PYTHON $SCRIPT --tts-url http://127.0.0.1:9000 --target-hours 1.5 --output data/sbvits2_training
