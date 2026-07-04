# Start the local ComfyUI image-gen tool (tools/ComfyUI, own venv).
# Open http://127.0.0.1:8188 after it prints "To see the GUI go to ...".
#
# This is the free/local image generator for Koroki's world art (see docs/local_image_gen_setup.md
# and docs/frontend_art_prompts.md). It is SEPARATE from the Koroki stack — own venv, own models.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$comfy = Join-Path $root "tools\ComfyUI"
$py = Join-Path $comfy "venv\Scripts\python.exe"

if (-not (Test-Path $py)) { Write-Host "ComfyUI venv missing — run the install first." -ForegroundColor Red; exit 1 }

# The machine's TLS interception breaks Python HTTPS cert verification (same issue pip/yt-dlp hit).
# Models are pre-downloaded locally so ComfyUI doesn't need the network for generation. If a node
# still tries an online fetch and fails on SSL, pre-download that file with: curl -kL <url> -o <path>.
Write-Host "Starting ComfyUI on http://127.0.0.1:8188 ..." -ForegroundColor Cyan
Set-Location $comfy
& $py main.py --port 8188
