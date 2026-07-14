# Local side of the pod rehearsal: tunnels + orchestrator + bot. NOTHING GPU local.
#   powershell -File start_rehearsal.ps1 -PodIp 1.2.3.4 -PodPort 12345
# Close this window (or Ctrl+C) to end: settings restore automatically.
# Afterwards: TERMINATE the pod, then normal life = .\scripts\launch_koroki.ps1.
param(
    [Parameter(Mandatory)][string]$PodIp,
    [Parameter(Mandatory)][int]$PodPort,
    [string]$Key = "$env:USERPROFILE\.ssh\runpod_battery"
)
$Root = "C:\Users\Shinn\Desktop\Koroki"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Settings = Join-Path $Root "config\settings.yaml"
$Backup = "$Settings.rehearsal_backup"
$Procs = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Start-Window {
    param([string]$Name, [string]$Command)
    $p = Start-Process cmd.exe -ArgumentList "/k", "title [Rehearsal] $Name && $Command" `
        -WorkingDirectory $Root -PassThru
    $Procs.Add($p)
}

Write-Host "=== Koroki 3090 rehearsal (pod $PodIp) ===" -ForegroundColor Cyan

# .env + process env, exactly like the real launcher
Get-Content (Join-Path $Root ".env") | ForEach-Object {
    if ($_ -match "^\s*([^#\s][^=]*)=(.*)$") {
        [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim().Trim('"').Trim("'"), "Process")
    }
}
$env:KOROKI_ROOT = $Root
$env:PYTHONPATH = $Root
$env:KOROKI_DEFER_TTS = "true"
$env:BRAIN_MODEL_PROFILE = "production"
$env:BRAIN_OLLAMA_MODEL = ""

# 1. settings: voice adapter -> :9000 (IndexTTS on pod). Backup + patch.
if (-not (Test-Path $Backup)) { Copy-Item $Settings $Backup }
$cfg = Get-Content $Settings -Raw
$cfg = $cfg -replace 'adapter_url: "http://127\.0\.0\.1:9004"', 'adapter_url: "http://127.0.0.1:9000"'
[System.IO.File]::WriteAllText($Settings, $cfg, (New-Object System.Text.UTF8Encoding $false))
Write-Host "[1/4] settings patched: voice -> :9000 (backup kept)" -ForegroundColor Yellow

try {
    # 1.5 clear a stale orchestrator — cmd-wrapper kills orphan python children
    # (live 2026-07-08: a zombie orchestrator with poisoned sleep state kept
    # unloading her voice). Port owner + tree-kill.
    $stale = netstat -ano | Select-String ":9882\s.*LISTENING" | Select-Object -First 1
    if ($stale) {
        $stalePid = ($stale -split '\s+')[-1]
        taskkill /PID $stalePid /T /F 2>$null | Out-Null
        Write-Host "  cleared stale orchestrator (pid $stalePid, tree)" -ForegroundColor DarkYellow
        Start-Sleep -Seconds 1
    }

    # 2. tunnels — pod services are localhost-only; these ARE the only doors
    Write-Host "[2/4] opening tunnels 9881/9000/9005..." -ForegroundColor Yellow
    Start-Window -Name "SSH tunnels (do not close)" -Command `
        "ssh -i `"$Key`" -p $PodPort -o ServerAliveInterval=15 -o ExitOnForwardFailure=yes -N -L 9881:127.0.0.1:9881 -L 9000:127.0.0.1:9000 -L 9005:127.0.0.1:9005 root@$PodIp"
    Start-Sleep -Seconds 4

    $ok = $true
    foreach ($port in 9881, 9000, 9005) {
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 10 | Out-Null
            Write-Host "  :$port healthy through tunnel" -ForegroundColor Green
        } catch {
            Write-Host "  :$port NOT reachable — is pod_run.sh done?" -ForegroundColor Red
            $ok = $false
        }
    }
    if (-not $ok) { throw "tunnel health failed — fix pod side, rerun" }

    # 3. local organs: orchestrator + bot (no supervisor — it would fight tunnels)
    Write-Host "[3/4] starting orchestrator + bot..." -ForegroundColor Yellow
    Start-Window -Name "Orchestrator :9882" -Command `
        "`"$VenvPython`" -m uvicorn `"services.orchestrator.app:app`" --host 0.0.0.0 --port 9882 --no-access-log"
    Start-Sleep -Seconds 4
    Start-Window -Name "Discord Bot" -Command `
        "set KOROKI_DEFER_TTS=true && `"$VenvPython`" `"$Root\discord_bot.py`""

    Write-Host "[4/4] REHEARSAL LIVE — she thinks/speaks/sees on the 3090." -ForegroundColor Cyan
    Write-Host "      Game + capture stay on your GPU. Ctrl+C here ends it." -ForegroundColor Gray
    while ($true) { Start-Sleep -Seconds 5 }
} finally {
    Write-Host "`nrestoring settings + stopping rehearsal windows..." -ForegroundColor Red
    Copy-Item $Backup $Settings -Force
    Remove-Item $Backup -Force
    foreach ($p in $Procs) {
        if (-not $p.HasExited) {
            # tree-kill: killing only the cmd wrapper orphans the python child
            taskkill /PID $p.Id /T /F 2>$null | Out-Null
        }
    }
    Write-Host "settings restored. Remember: TERMINATE the pod." -ForegroundColor Yellow
}
