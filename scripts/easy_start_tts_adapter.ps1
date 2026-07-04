#!/usr/bin/env powershell
# Koroki Ascension 2.0 - IndexTTS Adapter Startup (Python 3.11)
# Starts: IndexTTS HTTP adapter on port 9000
# Use this alongside main stack for faster TTS throughput.

param(
    [string]$Port = "9000",
    [string]$ModelDir = "experiments/index-tts/checkpoints",
    [string]$Device = $null,
    [switch]$UseFP32 = $false
)

Write-Host "Koroki Ascension 2.0 - IndexTTS Adapter Startup" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan

# Resolve repo root (parent of scripts/ directory)
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

# Activate Python 3.11 venv (IndexTTS compatible)
Write-Host "`nActivating Python 3.11 environment..." -ForegroundColor Yellow
if (!(Test-Path "$repoRoot\.venv_indextts")) {
    Write-Host "`nError: .venv_indextts not found. Create it first with:" -ForegroundColor Red
    Write-Host "  py -3.11 -m venv .venv_indextts" -ForegroundColor Red
    exit 1
}

& "$repoRoot\.venv_indextts\Scripts\Activate.ps1"

# Check for CUDA-capable torch — CPU-only torch makes TTS unusably slow
$cudaOk = & "$repoRoot\.venv_indextts\Scripts\python.exe" -c "import torch; print(torch.cuda.is_available())" 2>$null
if ($cudaOk.Trim() -ne "True") {
    Write-Host "`n[ERROR] .venv_indextts has CPU-only torch. IndexTTS will be extremely slow." -ForegroundColor Red
    Write-Host "  Fix: run this command, then restart:" -ForegroundColor Yellow
    Write-Host "    .\.venv_indextts\Scripts\pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128" -ForegroundColor White
    Write-Host ""
}

# Set environment variables for IndexTTS
$env:KOROKI_ROOT = $repoRoot
$env:INDEX_TTS_MODEL_DIR = Join-Path $repoRoot $ModelDir
$env:INDEX_TTS_USE_FP16 = if ($UseFP32) { "false" } else { "true" }
if ($Device) {
    $env:INDEX_TTS_DEVICE = $Device
}

Write-Host "`nIndexTTS Configuration:" -ForegroundColor Yellow
Write-Host "  Model Dir: $ModelDir"
Write-Host "  Device: $(if ($Device) { $Device } else { 'auto-detect (CUDA > MPS > CPU)' })"
Write-Host "  Use FP16: $(if ($UseFP32) { 'false' } else { 'true' })"
Write-Host "  Listening on: http://0.0.0.0:$Port"

Write-Host "`nStarting IndexTTS adapter..." -ForegroundColor Yellow
# Clean up any stale process on port 9000
Write-Host "`nCleaning up port $Port..." -ForegroundColor Yellow
& "$repoRoot\scripts\cleanup_port_9000.ps1"

Write-Host "`nStarting IndexTTS adapter..." -ForegroundColor Yellow
Write-Host "Press Ctrl+C to stop.`n" -ForegroundColor Cyan

python "$repoRoot\experiments\index-tts\adapter.py" --port $Port
