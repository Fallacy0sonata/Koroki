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

# Modernized 2026-07-05: production voice = CosyVoice :9004 (.venv_cosyvoice),
# eyes = vision :9005, supervisor watches everything (the brain-wedge answer).
# IndexTTS :9000 is the FALLBACK engine — start manually if needed:
#   .venv_indextts\Scripts\python.exe experiments\index-tts\adapter.py
Write-Host "[>>] Clearing stale Koroki ports..." -ForegroundColor DarkYellow
Stop-KorokiPorts -Ports @(9880, 9881, 9882, 9004, 9005)
Start-Sleep -Seconds 1

# OPT-O1: models.brain.engine picks the brain venv — exllamav2 only imports in
# .venv_brain2. settings.yaml is the single source of truth (supervisor reads the
# same key), so an engine flip is: edit settings.yaml, rerun this launcher.
$BrainEngine = & $VenvPython -c "from shared.utils.config import get_settings; print(get_settings().get('models',{}).get('brain',{}).get('engine','transformers'))" 2>$null
if ($LASTEXITCODE -ne 0 -or -not $BrainEngine) { $BrainEngine = "transformers" }
$BrainEngine = "$BrainEngine".Trim()
$BrainPython = $VenvPython
if ($BrainEngine -eq "exllamav2") {
    $BrainPython = Join-Path $Root ".venv_brain2\Scripts\python.exe"
    if (-not (Test-Path $BrainPython)) {
        Write-Error "engine=exllamav2 but .venv_brain2 is missing"
        exit 1
    }
    Write-Host "[OK] Brain engine: exllamav2 (.venv_brain2)" -ForegroundColor Green
}

$brainCmd  = "`"$BrainPython`" -m uvicorn `"services.brain.app:app`" --host 127.0.0.1 --port 9881 --no-access-log"
$ttsCmd    = "`"$VenvPython`" -m uvicorn `"services.tts.app:app`" --host 127.0.0.1 --port 9880 --no-access-log"
$orchCmd   = "`"$VenvPython`" -m uvicorn `"services.orchestrator.app:app`" --host 0.0.0.0 --port 9882 --no-access-log"
$visionCmd = "`"$VenvPython`" -m uvicorn `"services.vision.main:app`" --host 127.0.0.1 --port 9005 --no-access-log"
$botCmd    = "set KOROKI_DEFER_TTS=true && `"$VenvPython`" `"$Root\discord_bot.py`""
$supCmd    = "`"$VenvPython`" `"$Root\supervisor.py`" --mode $Mode"

$VenvCosyPython = Join-Path $Root ".venv_cosyvoice\Scripts\python.exe"
$cosyCmd = "`"$VenvCosyPython`" `"$Root\experiments\cosyvoice\adapter.py`" --port 9004"

Write-Host "[>>] Starting Brain..." -ForegroundColor Yellow
Start-KorokiWindow -Name "Brain :9881" -Command $brainCmd | Out-Null
Start-Sleep -Seconds 3

if ($QwenTTS) {
    Write-Host "[>>] Starting TTS (QwenTTS :9880, legacy)..." -ForegroundColor Yellow
    Start-KorokiWindow -Name "TTS :9880" -Command $ttsCmd | Out-Null
    Start-Sleep -Seconds 2
} elseif (-not (Test-Path $VenvCosyPython)) {
    Write-Host "[ERROR] .venv_cosyvoice not found — run scripts/setup_cosyvoice.ps1" -ForegroundColor Red
} else {
    Write-Host "[>>] Starting CosyVoice voice :9004 (production)..." -ForegroundColor Yellow
    Start-KorokiWindow -Name "CosyVoice :9004" -Command $cosyCmd | Out-Null
    Start-Sleep -Seconds 2
}

Write-Host "[>>] Starting Vision :9005 (her eyes)..." -ForegroundColor Yellow
Start-KorokiWindow -Name "Vision :9005" -Command $visionCmd | Out-Null
Start-Sleep -Seconds 2

Write-Host "[>>] Starting Orchestrator..." -ForegroundColor Yellow
Start-KorokiWindow -Name "Orchestrator :9882" -Command $orchCmd | Out-Null
Start-Sleep -Seconds 2

if ($Mode -in @("discord", "both")) {
    Write-Host "[>>] Starting Discord client..." -ForegroundColor Yellow
    Start-KorokiWindow -Name "Discord Bot" -Command $botCmd | Out-Null
    Start-Sleep -Seconds 1
}

Write-Host "[>>] Starting Supervisor (revives dead/wedged services)..." -ForegroundColor Yellow
Start-KorokiWindow -Name "Supervisor" -Command $supCmd | Out-Null

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
    Write-Host "  Voice:        http://127.0.0.1:9004/health (CosyVoice)" -ForegroundColor White
}
Write-Host "  Vision:       http://127.0.0.1:9005/health" -ForegroundColor White
Write-Host "  Orchestrator: http://127.0.0.1:9882/health" -ForegroundColor White
Write-Host "  Supervisor:   data/logs/supervisor.log (auto-revives wedged services)" -ForegroundColor White
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
