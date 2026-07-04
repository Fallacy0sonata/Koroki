# doctor.ps1 — Koroki environment diagnostic
#
# Fast pre-flight check before starting the stack or after any environment change.
# Verifies: the 4 venvs + Python versions, CUDA in the main stack, key model files,
# config/secrets, disk space, and whether the service ports are free or already listening.
#
# Run from Koroki root:   .\scripts\doctor.ps1
# Exit code 0 = all green, 1 = at least one FAIL.
#
# Reference: docs/environment_matrix.md, docs/checkpoint_manifest.md, docs/koroki_map.md.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$fails = 0
$warns = 0

function Ok($m)   { Write-Host "  [ OK ] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [WARN] $m" -ForegroundColor Yellow; $script:warns++ }
function Fail($m) { Write-Host "  [FAIL] $m" -ForegroundColor Red; $script:fails++ }
function Section($m) { Write-Host "`n== $m ==" -ForegroundColor Cyan }

# --- venvs + Python versions --------------------------------------------------
Section "Virtual environments (expect exactly 4)"
$venvs = @{
    ".venv"            = "3.12"
    ".venv_indextts"   = "3.11"
    ".venv_singing"    = "3.11"
    ".venv_diffsinger" = "3.11"
}
foreach ($v in $venvs.Keys) {
    $py = Join-Path $root "$v\Scripts\python.exe"
    if (Test-Path $py) {
        $ver = (& $py --version 2>&1) -replace "Python ", ""
        if ($ver.StartsWith($venvs[$v])) { Ok "$v -> Python $ver" }
        else { Fail "$v -> Python $ver (expected $($venvs[$v]).x)" }
    } else {
        Fail "$v missing ($py not found)"
    }
}
# Flag any stray/deleted venvs that shouldn't be here.
foreach ($dead in @(".venv_singing_v2", ".venv_cosyvoice", ".venv_fishspeech")) {
    if (Test-Path (Join-Path $root $dead)) { Warn "$dead exists but was meant to be deleted (cleanup 2026-06-27)" }
}

# --- CUDA in the main stack ---------------------------------------------------
Section "CUDA (main stack .venv)"
$mainpy = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $mainpy) {
    $cuda = & $mainpy -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)" 2>&1
    if ($cuda -match "^True") { Ok "torch CUDA available ($cuda)" }
    else { Fail "torch CUDA NOT available ($cuda) — wrong wheel in .venv? reinstall cu128 build" }
} else { Fail ".venv python missing — cannot check CUDA" }

# --- key model files ----------------------------------------------------------
Section "Model files (production path)"
$models = @{
    "DiffSinger base 160k"   = "experiments\diffsinger\DiffSinger\checkpoints\koroki_ja_v1_160k\model_ckpt_steps_160000.ckpt"
    "DiffSinger v12 @40000"  = "experiments\diffsinger\DiffSinger\checkpoints\koroki_v12\model_ckpt_steps_40000.ckpt"
    "NSF-HiFiGAN vocoder"    = "experiments\diffsinger\DiffSinger\checkpoints\pc_nsf_hifigan_44.1k_hop512_128bin_2025.02\pc_nsf_hifigan_44.1k_hop512_128bin_2025.02\model.ckpt"
    "RVC Korokiv5 weights"   = "adapters\singing\Korokiv5_300e_34500s_best_epoch.pth"
    "RVC Korokiv5 index"     = "adapters\singing\Korokiv5.index"
    "LoRA koroki_4b"         = "adapters\koroki_4b\adapter_model.safetensors"
    "IndexTTS gpt.pth"       = "experiments\index-tts\checkpoints\gpt.pth"
    "phoneme dict 63"        = "experiments\diffsinger\phonemes_63.txt"
}
foreach ($m in $models.Keys) {
    $p = Join-Path $root $models[$m]
    if (Test-Path $p) { Ok "$m" } else { Fail "$m missing: $($models[$m])" }
}

# --- config + secrets ---------------------------------------------------------
Section "Config & secrets"
if (Test-Path (Join-Path $root "config\settings.yaml")) { Ok "config/settings.yaml" } else { Fail "config/settings.yaml missing" }
if (Test-Path (Join-Path $root ".env")) {
    $env = Get-Content (Join-Path $root ".env") -Raw
    foreach ($k in @("DISCORD_TOKEN", "OWNER_DISCORD_ID", "INTERNAL_API_KEY", "HF_TOKEN")) {
        if ($env -match "(?m)^\s*$k\s*=\s*\S") { Ok ".env has $k" } else { Warn ".env missing or empty $k" }
    }
} else { Fail ".env missing" }

# --- disk space ---------------------------------------------------------------
Section "Disk space"
$drive = (Get-Item $root).PSDrive
$freeGB = [math]::Round($drive.Free / 1GB, 1)
if ($freeGB -gt 30) { Ok "$($drive.Name): $freeGB GB free" }
elseif ($freeGB -gt 10) { Warn "$($drive.Name): only $freeGB GB free (training/renders need headroom)" }
else { Fail "$($drive.Name): $freeGB GB free — critically low (we hit 100% before)" }

# --- ports --------------------------------------------------------------------
Section "Service ports (free = not yet started; listening = service up)"
$ports = @{ 9882 = "Orchestrator"; 9881 = "Brain"; 9000 = "IndexTTS" }
foreach ($port in $ports.Keys) {
    $listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listening) { Ok "$port ($($ports[$port])) — listening" }
    else { Write-Host "  [ -- ] $port ($($ports[$port])) — free" -ForegroundColor DarkGray }
}

# --- summary ------------------------------------------------------------------
Write-Host ""
if ($fails -eq 0 -and $warns -eq 0) { Write-Host "All checks passed." -ForegroundColor Green; exit 0 }
elseif ($fails -eq 0) { Write-Host "$warns warning(s), no failures." -ForegroundColor Yellow; exit 0 }
else { Write-Host "$fails failure(s), $warns warning(s)." -ForegroundColor Red; exit 1 }
