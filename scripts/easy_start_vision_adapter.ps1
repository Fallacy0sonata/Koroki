#!/usr/bin/env powershell
# Koroki Ascension 2.0 - Vision Service Startup (main .venv, Python 3.12)
# Starts: moondream2 vision adapter on port 9005 — her eyes.
# Model lazy-loads on first look (~11 s) and unloads after idle; VRAM is only
# occupied (~2.5 GB) while she is actually looking at something.

param(
    [string]$Port = "9005"
)

Write-Host "Koroki Ascension 2.0 - Vision Service Startup" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan

# Resolve repo root (parent of scripts/ directory)
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (!(Test-Path "$repoRoot\.venv")) {
    Write-Host "`nError: .venv not found (main Python 3.12 venv)." -ForegroundColor Red
    exit 1
}

$modelDir = "$repoRoot\tools\models\moondream2-2025-06-21"
if (!(Test-Path "$modelDir\model.safetensors")) {
    Write-Host "`nError: moondream2 weights not found at $modelDir" -ForegroundColor Red
    exit 1
}
if (!(Test-Path "$modelDir\tokenizer_starmie.json")) {
    Write-Host "`nError: tokenizer_starmie.json missing — the repo tokenizer.json is the" -ForegroundColor Red
    Write-Host "wrong (legacy) tokenizer. Download moondream/starmie-v1 tokenizer.json." -ForegroundColor Red
    exit 1
}

$env:KOROKI_ROOT = $repoRoot
$env:HF_HUB_OFFLINE = "1"   # AV HTTPS interception breaks python SSL; everything is local anyway

Write-Host "`nVision Configuration:" -ForegroundColor Yellow
Write-Host "  Model: moondream2-2025-06-21 (int4 via torchao tinygemm)"
Write-Host "  Listening on: http://127.0.0.1:$Port"
Write-Host "  Lazy-load + idle-unload (see config/settings.yaml 'vision:' block)"

Write-Host "`nStarting vision service..." -ForegroundColor Yellow
Write-Host "Press Ctrl+C to stop.`n" -ForegroundColor Cyan

& "$repoRoot\.venv\Scripts\python.exe" -m uvicorn services.vision.main:app --host 127.0.0.1 --port $Port
