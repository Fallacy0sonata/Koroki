# Extract LiSA vocal segments for DiffSinger training (spk_id=3, high-pitch speaker).
# LiSA is chosen specifically for her wide high-note range (above G5/A5) which covers
# pitch territory that koroki_clean, yoasobi, and ado data leave sparse.
# Run from Koroki root.

$python = "C:\Users\Shinn\Desktop\Koroki\.venv_diffsinger\Scripts\python.exe"
$script = "C:\Users\Shinn\Desktop\Koroki\experiments\diffsinger\sing_song.py"
$trainDir = "C:\Users\Shinn\Desktop\Koroki\data\diffsinger_raw\lisa"

$songs = @(
    "LiSA 紅蓮華",
    "LiSA 炎",
    "LiSA Oath Sign",
    "LiSA crossing field",
    "LiSA unlasting",
    "LiSA Rising Hope",
    "LiSA Shirogane",
    "LiSA HADASHi NO KISEKI"
)

foreach ($song in $songs) {
    Write-Host "--- Extracting: $song ---" -ForegroundColor Cyan
    & $python $script $song `
        --extract-training-data `
        --training-dir $trainDir `
        --no-search
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARN: extraction failed for '$song' (exit $LASTEXITCODE), continuing..." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Done. Check segment count:" -ForegroundColor Green
$csv = Join-Path $trainDir "transcriptions.csv"
if (Test-Path $csv) {
    $lines = (Get-Content $csv | Measure-Object -Line).Lines - 1
    Write-Host "  $lines segments in $csv" -ForegroundColor Green
} else {
    Write-Host "  No CSV found at $csv — all extractions may have failed" -ForegroundColor Red
}
