[CmdletBinding()]
param(
    [string]$Label = "web_trial",
    [string]$OrchestratorUrl = "http://127.0.0.1:9882"
)

$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$BenchScript = Join-Path $Root "scripts\benchmark_web_stack.py"

if (-not (Test-Path $VenvPython)) {
    Write-Error "Python venv not found at: $VenvPython"
    exit 1
}

if (-not (Test-Path $BenchScript)) {
    Write-Error "Benchmark script not found at: $BenchScript"
    exit 1
}

Write-Host ""
Write-Host "╔══════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   Koroki Web Benchmark Runner    ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""
Write-Host "[>>] Label: $Label" -ForegroundColor Yellow
Write-Host "[>>] Orchestrator: $OrchestratorUrl" -ForegroundColor Yellow
Write-Host ""

& $VenvPython $BenchScript --label $Label --orchestrator-url $OrchestratorUrl
