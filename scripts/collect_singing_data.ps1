param(
    [string]$SongsFile = "experiments\singing-v2\songs.txt",
    [int]$Steps = 30,
    [string]$WhisperModel = "medium"
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

$python = "$root\.venv_singing_v2\Scripts\python.exe"
$script = "$root\experiments\singing-v2\collect_training_data.py"
$songsPath = "$root\$SongsFile"

if (-not (Test-Path $python)) {
    Write-Error "singing_v2 venv not found at $python"
    exit 1
}
if (-not (Test-Path $songsPath)) {
    Write-Error "Songs file not found: $songsPath"
    exit 1
}

$env:KOROKI_ROOT = $root

Write-Host "Starting DiffSinger data collection..."
Write-Host "  Songs file : $songsPath"
Write-Host "  Steps      : $Steps"
Write-Host "  Whisper    : $WhisperModel"
Write-Host "  Output     : $root\data\singing_training"
Write-Host ""

& $python $script `
    --songs-file $songsPath `
    --steps $Steps `
    --whisper-model $WhisperModel `
    --repo-root $root
