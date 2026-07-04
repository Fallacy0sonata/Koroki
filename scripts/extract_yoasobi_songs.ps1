# Extracts real YOASOBI vocal data from 8 more songs into the dedicated
# YOASOBI training directory. Run after setup_yoasobi_dataset.py.
#
# Usage: .\scripts\extract_yoasobi_songs.ps1

$python   = "C:\Users\Shinn\Desktop\Koroki\.venv_diffsinger\Scripts\python.exe"
$singScript = "C:\Users\Shinn\Desktop\Koroki\experiments\diffsinger\sing_song.py"
$trainDir = "C:\Users\Shinn\Desktop\Koroki\data\diffsinger_raw\yoasobi"

# Songs not yet in the dataset. Queried as "YOASOBI <title>" so yt-dlp finds
# the official upload; instrumental search is skipped (not needed for extraction).
$songs = @(
    "YOASOBI 群青",
    "YOASOBI たぶん",
    "YOASOBI ハルカ",
    "YOASOBI ハッピーエンド",
    "YOASOBI セブンティーン",
    "YOASOBI もし僕が願えたなら",
    "YOASOBI あの夢をなぞって",
    "YOASOBI 優しい彗星"
)

$total = $songs.Count
$done  = 0
foreach ($song in $songs) {
    $done++
    Write-Host ""
    Write-Host "[$done/$total] Extracting: $song" -ForegroundColor Cyan
    & $python $singScript $song `
        --extract-training-data `
        --training-dir $trainDir `
        --no-search `
        --whisper-model medium
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  WARNING: extraction failed for '$song' (exit $LASTEXITCODE) — continuing" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "All done. Check sample count:" -ForegroundColor Green
$count = (Get-ChildItem "$trainDir\wavs" -Filter "*.wav" -ErrorAction SilentlyContinue).Count
Write-Host "  $trainDir\wavs\ => $count wav files"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Binarize: run binarize with configs/koroki_yoasobi_phase1.yaml"
Write-Host "  2. Train:    run train_diffsinger_phase1.ps1"
