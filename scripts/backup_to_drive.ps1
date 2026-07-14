# Koroki -> Google Drive backup ("KorokiVault", 2026-07-04)
#
# Two layers:
#   current/    - incremental robocopy mirror of bulk, rarely-changing irreplaceables
#                 (trained checkpoints, adapters, datasets). Only diffs upload after run 1.
#   snapshots/  - dated copies of her current source tree (including ignored internal docs
#                 and uncommitted files), mutable self, settings, .env, and full git bundle.
#                 Keeps the last 8.
#
# Everything here is trained weights or her lived state - things no download can
# restore. Re-downloadable models (FLUX, Qwen, moondream, IndexTTS...) are excluded.
#
# Run manually:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup_to_drive.ps1
# Weekly task:   registered as "KorokiVaultBackup" (Sundays 04:30)

$ErrorActionPreference = "Continue"
$Root  = "C:\Users\Shinn\Desktop\Koroki"
$Vault = "G:\My Drive\KorokiVault"
$Stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$Snap  = Join-Path $Vault "snapshots\$Stamp"
$Log   = Join-Path $Vault "backup_log.txt"

if (-not (Test-Path "G:\My Drive")) {
    Write-Output "FATAL: G:\My Drive not found - is Google Drive for Desktop running?"
    exit 1
}
New-Item -ItemType Directory -Force -Path "$Vault\current", $Snap | Out-Null

function Mirror($src, $dst) {
    if (-not (Test-Path $src)) { Write-Output "  skip (missing): $src"; return }
    # /MIR mirror, /FFT tolerant timestamps (virtual FS), quiet file lists
    robocopy $src $dst /MIR /FFT /R:2 /W:5 /NP /NFL /NDL | Select-Object -Last 7
    if ($LASTEXITCODE -ge 8) { Write-Output "  ROBOCOPY ERROR ($LASTEXITCODE): $src" }
    else { Write-Output "  ok: $src" }
}

function SnapshotSource($src, $dst) {
    # A git bundle contains committed refs only. Koroki intentionally keeps internal docs
    # ignored for a future portfolio export, and active arcs often span many uncommitted
    # files. Copy source-shaped files as well so a green backup really contains today's work.
    $fileMasks = @(
        "*.py", "*.js", "*.ts", "*.html", "*.css", "*.md", "*.toml",
        "*.yaml", "*.yml", "*.json", "*.jsonl", "*.ps1", "*.bat", "*.cmd",
        "*.txt", "*.ini", "*.cfg", "*.svg", "*.xml", "*.lock",
        ".gitignore", ".env.example"
    )
    $excludeDirs = @(
        ".git", ".pytest_cache", "__pycache__", "node_modules",
        ".venv", ".venv_brain2", ".venv_cosyvoice", ".venv_diffsinger",
        ".venv_indextts", ".venv_singing", "ApplioV3.6.2", "data", "logs",
        "adapters", "assets", "checkpoints", "voice_samples",
        "stockfish-windows-x86-64-avx2", "unsloth_compiled_cache",
        "_unsloth_sentencepiece_temp",
        "$src\.claude\worktrees",
        "$src\tools\ComfyUI", "$src\tools\models", "$src\tools\mediamtx",
        "$src\experiments\index-tts\checkpoints",
        "$src\experiments\index-tts\vocos_assets",
        "$src\experiments\cosyvoice\pretrained_models",
        "$src\experiments\diffsinger\DiffSinger\checkpoints",
        "$src\experiments\wavebench\.venv"
    )
    $args = @($src, $dst) + $fileMasks + @(
        "/S", "/FFT", "/R:2", "/W:5", "/NP", "/NFL", "/NDL", "/XJ", "/XD"
    ) + $excludeDirs
    & robocopy @args | Select-Object -Last 7
    if ($LASTEXITCODE -ge 8) { Write-Output "  ROBOCOPY ERROR ($LASTEXITCODE): source snapshot" }
    else { Write-Output "  ok: source tree (tracked + untracked + ignored internal docs)" }
}

Write-Output "=== KorokiVault backup $Stamp ==="

# --- Layer 1: bulk mirror (incremental) ---------------------------------
Write-Output "[1/4] bulk mirror -> current/"
$ck = "$Root\experiments\diffsinger\DiffSinger\checkpoints"
Mirror "$ck\koroki_v12"                                  "$Vault\current\diffsinger_checkpoints\koroki_v12"
Mirror "$ck\koroki_ja_v1_160k"                           "$Vault\current\diffsinger_checkpoints\koroki_ja_v1_160k"
Mirror "$ck\pc_nsf_hifigan_44.1k_hop512_128bin_2025.02"  "$Vault\current\diffsinger_checkpoints\pc_nsf_hifigan_44.1k_hop512_128bin_2025.02"
Mirror "$Root\adapters"                                  "$Vault\current\adapters"
Mirror "$Root\tools\ComfyUI\models\loras"                "$Vault\current\comfy_loras"
# Training datasets moved to "G:\My Drive\Koroki Storage\datasets" (2026-07-05) —
# their primary copies already live on Drive, so no mirror needed here.
Mirror "$Root\voice_samples"                             "$Vault\current\voice_samples"
Mirror "$Root\assets"                                    "$Vault\current\assets"

# --- Layer 2: dated snapshot of source, including the working tree ------
Write-Output "[2/4] source snapshot -> snapshots/$Stamp"
git -C $Root bundle create "$Snap\koroki_code.bundle" --all 2>$null
if (Test-Path "$Snap\koroki_code.bundle") { Write-Output "  ok: git bundle" }
else { Write-Output "  WARN: git bundle failed" }
SnapshotSource $Root "$Snap\source_worktree"
git -C $Root status --short --branch | Set-Content "$Snap\git_status.txt" -Encoding UTF8
git -C $Root rev-parse HEAD | Set-Content "$Snap\git_head.txt" -Encoding ASCII

# --- Layer 3: dated snapshot of her mutable self ------------------------
Write-Output "[3/4] mutable state -> snapshots/$Stamp"

foreach ($d in @("data\memory", "data\koroki", "data\mind", "data\discord")) {
    $src = Join-Path $Root $d
    if (Test-Path $src) {
        robocopy $src (Join-Path $Snap ($d -replace '\\', '_')) /E /FFT /R:2 /W:5 /NP /NFL /NDL | Out-Null
        Write-Output "  ok: $d"
    }
}
Copy-Item "$Root\config\settings.yaml" $Snap -ErrorAction SilentlyContinue
Copy-Item "$Root\.env" $Snap -ErrorAction SilentlyContinue
$claudeMem = "C:\Users\Shinn\.claude\projects\C--Users-Shinn-Desktop-Koroki\memory"
if (Test-Path $claudeMem) {
    robocopy $claudeMem "$Snap\claude_memory" /E /FFT /R:2 /W:5 /NP /NFL /NDL | Out-Null
    Write-Output "  ok: claude project memory"
}

# --- Layer 4: prune old snapshots (keep newest 8) ------------------------
Write-Output "[4/4] prune"
$old = Get-ChildItem "$Vault\snapshots" -Directory | Sort-Object Name -Descending | Select-Object -Skip 8
foreach ($o in $old) {
    Remove-Item $o.FullName -Recurse -Force -Confirm:$false
    Write-Output "  pruned: $($o.Name)"
}

$size = (Get-ChildItem $Snap -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
"{0}  snapshot={1:N0} MB" -f $Stamp, $size | Add-Content $Log
Write-Output ("done - snapshot {0:N0} MB (bulk mirror uploads in background via Drive client)" -f $size)
