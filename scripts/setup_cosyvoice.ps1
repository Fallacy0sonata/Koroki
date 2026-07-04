# One-time setup for CosyVoice adapter.
# Clones the repo, creates venv, installs deps, downloads CosyVoice2-0.5B.

$ErrorActionPreference = "Continue"
$ROOT = Split-Path -Parent $PSScriptRoot
$CV_DIR = "$ROOT\experiments\cosyvoice"
$CV_REPO = "$CV_DIR\CosyVoice"
$VENV = "$ROOT\.venv_cosyvoice"
$MODELS_DIR = "$CV_DIR\pretrained_models"

Write-Host "=== CosyVoice Setup ===" -ForegroundColor Cyan

# 1. Clone repo
if (-not (Test-Path $CV_REPO)) {
    Write-Host "[1/5] Cloning CosyVoice..." -ForegroundColor Yellow
    git clone --recursive https://github.com/FunAudioLLM/CosyVoice $CV_REPO
} else {
    Write-Host "[1/5] CosyVoice repo already exists, skipping clone." -ForegroundColor Green
}

# 2. Create venv (Python 3.11 for broader compatibility with CosyVoice deps)
if (-not (Test-Path "$VENV\Scripts\python.exe")) {
    Write-Host "[2/5] Creating Python 3.11 venv..." -ForegroundColor Yellow
    $py311 = Get-Command python3.11 -ErrorAction SilentlyContinue
    if (-not $py311) {
        $py311 = Get-Command python -ErrorAction SilentlyContinue
    }
    & $py311.Source -m venv $VENV
} else {
    Write-Host "[2/5] Venv already exists." -ForegroundColor Green
}

$PIP = "$VENV\Scripts\pip.exe"
$PYTHON = "$VENV\Scripts\python.exe"
$SSL_FLAGS = @("--trusted-host", "pypi.org", "--trusted-host", "files.pythonhosted.org", "--trusted-host", "download.pytorch.org")

# 3. Install PyTorch (CUDA 12.8)
Write-Host "[3/5] Installing PyTorch CUDA 12.8..." -ForegroundColor Yellow
# setuptools must come first — openai-whisper's build needs pkg_resources
& $PIP install setuptools wheel $SSL_FLAGS
& $PIP install torch torchaudio --index-url https://download.pytorch.org/whl/cu128 `
    --trusted-host download.pytorch.org --trusted-host pypi.org --trusted-host files.pythonhosted.org

# 4. Install CosyVoice requirements
Write-Host "[4/5] Installing CosyVoice requirements..." -ForegroundColor Yellow
Set-Location $CV_REPO

# openai-whisper has a broken setup.py (missing pkg_resources) on newer pip.
# We don't need it — it's only used for the ASR web demo, not TTS synthesis.
# Strip it from requirements before installing.
$filteredReqs = Get-Content "$CV_REPO\requirements.txt" | Where-Object {
    $_ -notmatch 'openai.whisper' -and $_ -notmatch 'openai_whisper'
}
$filteredReqs | Out-File -Encoding utf8 "$CV_REPO\requirements_no_whisper.txt"
Write-Host "  Installing requirements (openai-whisper excluded — not needed for TTS)" -ForegroundColor DarkGray
& $PIP install -r "$CV_REPO\requirements_no_whisper.txt" $SSL_FLAGS

# CosyVoice has no setup.py/pyproject.toml — not installable as a package.
# The adapter injects it into sys.path at runtime instead.

# Install adapter deps
& $PIP install fastapi uvicorn soundfile scipy pydantic $SSL_FLAGS

# wetext provides Japanese/English text normalization for CosyVoice's text frontend.
# Without it, CosyVoice logs "no frontend available" and skips normalization,
# which corrupts speech token sequences on complex Japanese text → drum/noise artifacts.
& $PIP install wetext $SSL_FLAGS

# openai-whisper: CosyVoice imports it at model init time (not just the ASR demo).
# setuptools is already installed above so pkg_resources is available.
& $PIP install openai-whisper $SSL_FLAGS

# 5. Download CosyVoice2-0.5B model
Write-Host "[5/5] Downloading CosyVoice2-0.5B..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $MODELS_DIR | Out-Null

# Try huggingface_hub first
& $PYTHON -c @"
import ssl, urllib.request, sys, os

# Bypass SSL issues
ssl._create_default_https_context = ssl._create_unverified_context
os.environ['HF_HUB_DISABLE_SSL_VERIFICATION'] = '1'

try:
    from huggingface_hub import snapshot_download
    print('Downloading via huggingface_hub...')
    snapshot_download(
        repo_id='FunAudioLLM/CosyVoice2-0.5B',
        local_dir=r'$MODELS_DIR\CosyVoice2-0.5B',
        ignore_patterns=['*.git*', '*.md'],
    )
    print('Download complete.')
except Exception as e:
    print(f'HuggingFace download failed: {e}')
    print('Try ModelScope: pip install modelscope && modelscope download --model iic/CosyVoice2-0.5B')
    sys.exit(1)
"@

Set-Location $ROOT
Write-Host "`n=== Setup complete ===" -ForegroundColor Green
Write-Host "Start the adapter with: .\scripts\easy_start_cosyvoice_adapter.ps1" -ForegroundColor Cyan
