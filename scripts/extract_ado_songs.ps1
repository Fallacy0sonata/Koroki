# Extract ado vocal segments for DiffSinger training (spk_id=2, high-pitch speaker).
# Target: 200-300 segments covering ado's wide pitch range, especially high notes.
# Run from Koroki root.

$python = "C:\Users\Shinn\Desktop\Koroki\.venv_diffsinger\Scripts\python.exe"
$script = "C:\Users\Shinn\Desktop\Koroki\experiments\diffsinger\sing_song.py"
$trainDir = "C:\Users\Shinn\Desktop\Koroki\data\diffsinger_raw\ado"

$songs = @(
    "ado うっせぇわ",
    "ado 新時代",
    "ado 踊",
    "ado ギラギラ",
    "ado 逆夢",
    "ado 阿修羅ちゃん",
    "ado いばら",
    "ado 私は最強"
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
