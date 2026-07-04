[CmdletBinding()]
param(
    [ValidateSet("discord", "web", "both")]
    [string]$Mode = "web",
    [string]$BrainProfileOverride = "",
    [string]$OllamaBrainModel = "",
    [switch]$DisableOllamaBrain,
    [switch]$QwenTTS   # Legacy: start QwenTTS (:9880) instead of IndexTTS (:9000)
)

$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Procs = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Stop-KorokiPorts {
    param([int[]]$Ports)

    foreach ($port in $Ports) {
        $line = netstat -ano | Select-String ":$port\s" | Where-Object { $_ -match "LISTENING" } | Select-Object -First 1
        if (-not $line) {
            continue
        }
        $pid_ = ($line -split '\s+')[-1]
        if ($pid_ -match '^\d+$' -and $pid_ -ne '0') {
            try {
                Stop-Process -Id ([int]$pid_) -Force -ErrorAction Stop
                Write-Host "  [OK] Cleared PID $pid_ on :$port" -ForegroundColor DarkYellow
            } catch {
                Write-Host "  [SKIP] PID $pid_ on :$port already gone" -ForegroundColor Gray
            }
        }
    }
}

function Start-KorokiWindow {
    param(
        [string]$Name,
        [string]$Command
    )

    $proc = Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList "/k", "title [Koroki] $Name && $Command" `
        -WorkingDirectory $Root `
        -PassThru `
        -WindowStyle Normal
    $Procs.Add($proc)
    return $proc
}

Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║      Koroki Unified Launcher         ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $VenvPython)) {
    Write-Error "Python venv not found at: $VenvPython"
    exit 1
}

$EnvFile = Join-Path $Root ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match "^\s*([^#\s][^=]*)=(.*)$") {
            $key = $matches[1].Trim()
            $val = $matches[2].Trim().Trim('"').Trim("'")
            [System.Environment]::SetEnvironmentVariable($key, $val, "Process")
        }
    }
    Write-Host "[OK] Loaded .env" -ForegroundColor Green
}

$env:KOROKI_ROOT = $Root
$env:PYTHONPATH = $Root
$env:KOROKI_DEFER_TTS = "true"
$env:KOROKI_WEB_PERSISTENT_TTS = "true"
if ($BrainProfileOverride) {
    $env:BRAIN_MODEL_PROFILE = $BrainProfileOverride
} else {
    $env:BRAIN_MODEL_PROFILE = "production"
}
if ($DisableOllamaBrain) {
    $env:BRAIN_OLLAMA_MODEL = ""
} elseif ($OllamaBrainModel) {
    $env:BRAIN_OLLAMA_MODEL = $OllamaBrainModel
} else {
    $ollamaUp = $false
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:11434/" -UseBasicParsing -TimeoutSec 1 | Out-Null
        $ollamaUp = $true
    } catch {
        $ollamaUp = $false
    }
    
    # LoRA adapter (koroki_4b) requires local model loading — Ollama path bypasses adapters.
    # Ollama was used pre-LoRA; now disabled so brain loads Qwen3-4B-Thinking-2507 locally.
    $env:BRAIN_OLLAMA_MODEL = ""
    if (-not $ollamaUp) {
        Write-Host "[INFO] Ollama not detected — using local brain (expected)." -ForegroundColor DarkGray
    }
}

Write-Host "[>>] Clearing stale Koroki ports..." -ForegroundColor DarkYellow
Stop-KorokiPorts -Ports @(9880, 9881, 9882)
Start-Sleep -Seconds 1

$brainCmd = "`"$VenvPython`" -m uvicorn `"services.brain.app:app`" --host 127.0.0.1 --port 9881 --no-access-log"
$ttsCmd   = "`"$VenvPython`" -m uvicorn `"services.tts.app:app`" --host 127.0.0.1 --port 9880 --no-access-log"
$orchCmd  = "`"$VenvPython`" -m uvicorn `"services.orchestrator.app:app`" --host 0.0.0.0 --port 9882 --no-access-log"
$botCmd   = "set KOROKI_DEFER_TTS=true && `"$VenvPython`" `"$Root\discord_bot.py`""

$VenvIndexPython = Join-Path $Root ".venv_indextts\Scripts\python.exe"
$indexTtsCmd = "`"$VenvIndexPython`" `"$Root\experiments\index-tts\adapter.py`""

Write-Host "[>>] Starting Brain..." -ForegroundColor Yellow
Start-KorokiWindow -Name "Brain :9881" -Command $brainCmd | Out-Null
Start-Sleep -Seconds 3

if ($QwenTTS) {
    Write-Host "[>>] Starting TTS (QwenTTS :9880, legacy)..." -ForegroundColor Yellow
    Start-KorokiWindow -Name "TTS :9880" -Command $ttsCmd | Out-Null
    Start-Sleep -Seconds 2
} else {
    if (-not (Test-Path $VenvIndexPython)) {
        Write-Host "[ERROR] .venv_indextts not found — IndexTTS cannot start. Create it with: py -3.11 -m venv .venv_indextts" -ForegroundColor Red
    } else {
        Write-Host "[>>] Starting IndexTTS adapter :9000 (Python 3.11)..." -ForegroundColor Yellow
        Start-KorokiWindow -Name "IndexTTS :9000" -Command $indexTtsCmd | Out-Null
        Start-Sleep -Seconds 2
    }
}

Write-Host "[>>] Starting Orchestrator..." -ForegroundColor Yellow
Start-KorokiWindow -Name "Orchestrator :9882" -Command $orchCmd | Out-Null
Start-Sleep -Seconds 2

if ($Mode -in @("discord", "both")) {
    Write-Host "[>>] Starting Discord client..." -ForegroundColor Yellow
    Start-KorokiWindow -Name "Discord Bot" -Command $botCmd | Out-Null
    Start-Sleep -Seconds 1
}

Write-Host ""
Write-Host "[OK] Koroki mode: $Mode" -ForegroundColor Green
if ($env:BRAIN_MODEL_PROFILE) {
    Write-Host "  Brain profile: $($env:BRAIN_MODEL_PROFILE)" -ForegroundColor White
}
if ($env:BRAIN_OLLAMA_MODEL) {
    Write-Host "  Ollama brain:  $($env:BRAIN_OLLAMA_MODEL)" -ForegroundColor White
}
Write-Host "  Brain:        http://127.0.0.1:9881/health" -ForegroundColor White
if ($QwenTTS) {
    Write-Host "  TTS:          http://127.0.0.1:9880/health (QwenTTS legacy)" -ForegroundColor White
} else {
    Write-Host "  IndexTTS:     http://127.0.0.1:9000/health" -ForegroundColor White
}
Write-Host "  Orchestrator: http://127.0.0.1:9882/health" -ForegroundColor White
if ($Mode -in @("web", "both")) {
    Write-Host "  Web Client:   http://127.0.0.1:9882/" -ForegroundColor White
}
if ($Mode -in @("discord", "both")) {
    Write-Host "  Discord:      two-resident mode (Brain + TTS)" -ForegroundColor White
}
Write-Host ""
Write-Host "Press Ctrl+C in this launcher window to stop everything it started." -ForegroundColor Gray
Write-Host ""

$WarnedPids = [System.Collections.Generic.HashSet[int]]::new()
try {
    while ($true) {
        Start-Sleep -Seconds 5
        foreach ($proc in @($Procs)) {
            if ($proc.HasExited -and $WarnedPids.Add($proc.Id)) {
                Write-Host "[WARN] Window process (PID $($proc.Id)) exited." -ForegroundColor Red
            }
        }
    }
} finally {
    Write-Host "`n[Koroki] Shutting down launched processes..." -ForegroundColor Red
    foreach ($proc in $Procs) {
        if (-not $proc.HasExited) {
            $proc.Kill()
        }
    }
}
