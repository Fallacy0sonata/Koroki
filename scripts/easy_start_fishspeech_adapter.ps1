# Start Fish Speech adapter for Koroki singing data generation.
# Port: 9003 | Model: fish-speech-1.5 | Venv: .venv_fishspeech

$python = "C:\Users\Shinn\Desktop\Koroki\.venv_fishspeech\Scripts\python.exe"
$adapter = "C:\Users\Shinn\Desktop\Koroki\experiments\fish-speech\adapter.py"

if (-not (Test-Path $python)) {
    Write-Error "Fish Speech venv not found. Run setup first."
    exit 1
}

Write-Host "Starting Fish Speech adapter on port 9003..." -ForegroundColor Cyan
Write-Host "Model: fish-speech-1.5 (Japanese voice cloning)" -ForegroundColor Gray
Write-Host "Reference: voice_samples/JP_sample1.wav + EN_sample.wav" -ForegroundColor Gray
Write-Host ""

& $python $adapter --port 9003
