$ROOT = "C:\Users\Shinn\Desktop\Koroki"
$DS   = "$ROOT\experiments\diffsinger\DiffSinger"
$CKP  = "$DS\checkpoints"

Write-Host "=== Koroki Storage Cleanup ===" -ForegroundColor Cyan
Write-Host "Calculating space before cleanup..."
$before = (Get-ChildItem $ROOT -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1GB
Write-Host ("Before: {0:F1} GB" -f $before) -ForegroundColor Yellow
Write-Host ""

function Remove-Dir($path) {
    if (Test-Path $path) {
        Write-Host "  DEL dir  $path" -ForegroundColor Red
        Remove-Item $path -Recurse -Force -ErrorAction SilentlyContinue
    }
}
function Remove-File($path) {
    if (Test-Path $path) {
        $sz = (Get-Item $path).Length / 1MB
        Write-Host ("  DEL file {0}  ({1:F0} MB)" -f $path, $sz) -ForegroundColor Red
        Remove-Item $path -Force -ErrorAction SilentlyContinue
    }
}

# ── 1. Obsolete DiffSinger experiments (~68 GB) ──────────────────────────────
Write-Host "--- Obsolete experiments ---" -ForegroundColor Cyan
Remove-Dir "$CKP\koroki_v2"
Remove-Dir "$CKP\koroki_v3"
Remove-Dir "$CKP\koroki_ja_old"
Remove-Dir "$CKP\koroki_clean_v1"
Remove-Dir "$CKP\koroki_multispk_v2"
Remove-Dir "$CKP\koroki_multispk_v3"
Remove-Dir "$CKP\koroki_multispk_v4"
Remove-Dir "$CKP\koroki_multispk_v5"
Remove-Dir "$CKP\koroki_multispk_phase2"
Remove-Dir "$CKP\koroki_multispk_v3_expanded"
Remove-Dir "$CKP\koroki_multispk_v4_expanded"
Remove-Dir "$CKP\koroki_yoasobi_phase1"

# ── 2. Loose misplaced checkpoints in DiffSinger root (~11 GB) ───────────────
Write-Host "--- Loose checkpoints in DiffSinger root ---" -ForegroundColor Cyan
Get-Item "$DS\model_ckpt_steps_*.ckpt" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host ("  DEL file {0}  ({1:F0} MB)" -f $_.FullName, ($_.Length / 1MB)) -ForegroundColor Red
    Remove-Item $_.FullName -Force
}

# ── 3. DiffSinger root lightning_logs (~1.6 GB) ───────────────────────────────
Write-Host "--- DiffSinger root lightning_logs ---" -ForegroundColor Cyan
Remove-Dir "$DS\lightning_logs"

# ── 4. koroki_v5: keep only 80k, delete intermediate steps (~7.5 GB) ─────────
Write-Host "--- koroki_v5 intermediate checkpoints (keeping 80k) ---" -ForegroundColor Cyan
$v5keep = "model_ckpt_steps_80000.ckpt"
Get-ChildItem "$CKP\koroki_v5\model_ckpt_steps_*.ckpt" -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne $v5keep } | ForEach-Object {
    Write-Host ("  DEL file {0}  ({1:F0} MB)" -f $_.Name, ($_.Length / 1MB)) -ForegroundColor Red
    Remove-Item $_.FullName -Force
}
Remove-Dir "$CKP\koroki_v5\lightning_logs"

# ── 5. koroki_v6: keep only 160k, delete all others (~16 GB) ─────────────────
Write-Host "--- koroki_v6 intermediate checkpoints (keeping 160k) ---" -ForegroundColor Cyan
$v6keep = "model_ckpt_steps_160000.ckpt"
Get-ChildItem "$CKP\koroki_v6\model_ckpt_steps_*.ckpt" -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne $v6keep } | ForEach-Object {
    Write-Host ("  DEL file {0}  ({1:F0} MB)" -f $_.Name, ($_.Length / 1MB)) -ForegroundColor Red
    Remove-Item $_.FullName -Force
}
Remove-Dir "$CKP\koroki_v6\lightning_logs"

# ── 6. koroki_ja_v1_160k: keep only 160k + lightning_logs gone (~7 GB) ───────
Write-Host "--- koroki_ja_v1_160k intermediate checkpoints (keeping 160k) ---" -ForegroundColor Cyan
$base_keep = "model_ckpt_steps_160000.ckpt"
Get-ChildItem "$CKP\koroki_ja_v1_160k\model_ckpt_steps_*.ckpt" -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne $base_keep } | ForEach-Object {
    Write-Host ("  DEL file {0}  ({1:F0} MB)" -f $_.Name, ($_.Length / 1MB)) -ForegroundColor Red
    Remove-Item $_.FullName -Force
}
Remove-Dir "$CKP\koroki_ja_v1_160k\lightning_logs"

# ── 7. Unused venvs (~17.9 GB) ────────────────────────────────────────────────
Write-Host "--- Unused venvs ---" -ForegroundColor Cyan
Remove-Dir "$ROOT\.venv_fishspeech"
Remove-Dir "$ROOT\.venv_singing_v2"
Remove-Dir "$ROOT\.venv_cosyvoice"

# ── 8. Applio training logs (~13 GB) ──────────────────────────────────────────
Write-Host "--- Applio logs ---" -ForegroundColor Cyan
Remove-Dir "$ROOT\ApplioV3.6.2\logs"

# ── 9. Data caches and staging (~7.1 GB) ──────────────────────────────────────
Write-Host "--- Data caches ---" -ForegroundColor Cyan
Remove-Dir "$ROOT\data\debug_stems"
Remove-Dir "$ROOT\data\diffsinger_work"
Remove-Dir "$ROOT\data\diffsinger_staging"

# ── 10. Loose audio files in root (~92 MB) ────────────────────────────────────
Write-Host "--- Loose root files ---" -ForegroundColor Cyan
Remove-File "$ROOT\acapella.wav"
Remove-File "$ROOT\acapella_converted.wav"

# ── Done ───────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Cleanup complete. Calculating final size (may take a moment)..." -ForegroundColor Cyan
$after = (Get-ChildItem $ROOT -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1GB
Write-Host ("After:  {0:F1} GB" -f $after) -ForegroundColor Green
Write-Host ("Freed:  {0:F1} GB" -f ($before - $after)) -ForegroundColor Green
