$python = "C:\Users\Shinn\Desktop\Koroki\.venv_diffsinger\Scripts\python.exe"
$trainDir = "C:\Users\Shinn\Desktop\Koroki\experiments\diffsinger\DiffSinger"
$config = "configs/koroki_ja.yaml"
$maxRestarts = 50

function Kill-TrainingWorkers {
    # Kill lingering python worker processes from a crashed training run.
    # Without this, stale DataLoader IPC channels deadlock the next restart.
    $victims = Get-Process -Name "python" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $python }
    foreach ($p in $victims) {
        try { $p.Kill(); $p.WaitForExit(3000) } catch {}
    }
}

Push-Location $trainDir
try {
    for ($i = 1; $i -le $maxRestarts; $i++) {
        Write-Host "--- Training run $i / $maxRestarts ---" -ForegroundColor Cyan
        & $python scripts/train.py acoustic --config $config
        $exit = $LASTEXITCODE
        if ($exit -eq 0) {
            Write-Host "Training completed successfully." -ForegroundColor Green
            break
        }
        Write-Host "Process exited with code $exit — cleaning up, restarting in 5s..." -ForegroundColor Yellow
        Kill-TrainingWorkers
        Start-Sleep -Seconds 5
    }
} finally {
    Pop-Location
}
