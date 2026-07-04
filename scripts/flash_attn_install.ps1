# flash_attn_install.ps1
# Installs flash-attn from source into a Koroki venv.
# Requires:
#   1. Visual Studio Build Tools 2022 (Desktop development with C++ workload)
#   2. CUDA Toolkit 12.8 for Windows x64
#
# Usage:
#   .\scripts\flash_attn_install.ps1              # installs into .venv (Python 3.12, main stack)
#   .\scripts\flash_attn_install.ps1 -Venv indextts  # installs into .venv_indextts (Python 3.11, IndexTTS)

param(
    [ValidateSet("main", "indextts")]
    [string]$Venv = "main"
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$CUDA_VERSION = "12.8"
$CUDA_HOME_PATH = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v$CUDA_VERSION"

if ($Venv -eq "indextts") {
    $VENV_PATH = Join-Path $repoRoot ".venv_indextts"
    $VENV_LABEL = ".venv_indextts (Python 3.11 — IndexTTS)"
} else {
    $VENV_PATH = Join-Path $repoRoot ".venv"
    $VENV_LABEL = ".venv (Python 3.12 — main stack)"
}

Write-Host ""
Write-Host "=== Flash-Attn Installer ===" -ForegroundColor Cyan
Write-Host "Target venv: $VENV_LABEL" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $VENV_PATH)) {
    Write-Host "ERROR: venv not found at $VENV_PATH" -ForegroundColor Red
    exit 1
}

$PIP = Join-Path $VENV_PATH "Scripts\pip.exe"
$PYTHON = Join-Path $VENV_PATH "Scripts\python.exe"

# --- Step 1: Find cl.exe via vswhere ---
Write-Host "[1/5] Locating MSVC compiler (cl.exe)..." -ForegroundColor Yellow
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) {
    Write-Host "  ERROR: Visual Studio installer not found." -ForegroundColor Red
    Write-Host "  Install 'Visual Studio Build Tools 2022' with 'Desktop development with C++' workload." -ForegroundColor Red
    Write-Host "  Download: https://aka.ms/vs/17/release/vs_BuildTools.exe" -ForegroundColor White
    exit 1
}

$vsInstallPath = & $vswhere -latest -products * -requires Microsoft.VisualCpp.Tools.HostX64.TargetX64 -property installationPath
if (-not $vsInstallPath) {
    Write-Host "  ERROR: No VS installation with C++ tools found." -ForegroundColor Red
    exit 1
}
Write-Host "  Found VS at: $vsInstallPath" -ForegroundColor Green

$vsDevCmd = "$vsInstallPath\VC\Auxiliary\Build\vcvars64.bat"
if (-not (Test-Path $vsDevCmd)) {
    Write-Host "  ERROR: vcvars64.bat not found at $vsDevCmd" -ForegroundColor Red
    exit 1
}
Write-Host "  Running vcvars64.bat to set MSVC environment..." -ForegroundColor Yellow
cmd.exe /c "`"$vsDevCmd`" >nul 2>&1 && set" | ForEach-Object {
    if ($_ -match "^([^=]+)=(.*)$") {
        [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
    }
}
$env:DISTUTILS_USE_SDK = "1"
Write-Host "  MSVC environment set." -ForegroundColor Green

# --- Step 2: Verify nvcc ---
Write-Host ""
Write-Host "[2/5] Verifying CUDA Toolkit ($CUDA_VERSION)..." -ForegroundColor Yellow
if (-not (Test-Path "$CUDA_HOME_PATH\bin\nvcc.exe")) {
    Write-Host "  ERROR: nvcc.exe not found at $CUDA_HOME_PATH\bin\nvcc.exe" -ForegroundColor Red
    exit 1
}
Write-Host "  nvcc found OK." -ForegroundColor Green

# --- Step 3: Set environment variables ---
Write-Host ""
Write-Host "[3/5] Setting CUDA_HOME and PATH..." -ForegroundColor Yellow
$env:CUDA_HOME = $CUDA_HOME_PATH
$env:CUDA_PATH = $CUDA_HOME_PATH
$env:PATH = "$CUDA_HOME_PATH\bin;$CUDA_HOME_PATH\libnvvp;$env:PATH"
Write-Host "  CUDA_HOME = $env:CUDA_HOME" -ForegroundColor Green
$nvccVer = & "$CUDA_HOME_PATH\bin\nvcc.exe" --version 2>&1 | Select-String "release"
Write-Host "  $nvccVer" -ForegroundColor Green

# --- Step 4: Build settings ---
Write-Host ""
Write-Host "[4/5] Configuring build settings..." -ForegroundColor Yellow
if (-not $env:MAX_JOBS)               { $env:MAX_JOBS = "1" }
if (-not $env:TORCH_CUDA_ARCH_LIST)   { $env:TORCH_CUDA_ARCH_LIST = "8.9" }
Write-Host "  MAX_JOBS = $env:MAX_JOBS" -ForegroundColor Green
Write-Host "  TORCH_CUDA_ARCH_LIST = $env:TORCH_CUDA_ARCH_LIST" -ForegroundColor Green

# --- Step 5: Install ---
Write-Host ""
Write-Host "[5/5] Installing flash-attn into $VENV_LABEL (20-60 min)..." -ForegroundColor Yellow
Write-Host "  Do NOT close this window." -ForegroundColor White
Write-Host ""

& $PIP install flash-attn --no-build-isolation

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "=== flash-attn installed successfully into $VENV_LABEL ===" -ForegroundColor Green
    & $PYTHON -c "import flash_attn; print('flash_attn version:', flash_attn.__version__)"
} else {
    Write-Host ""
    Write-Host "=== ERROR: flash-attn installation failed ===" -ForegroundColor Red
    exit 1
}
