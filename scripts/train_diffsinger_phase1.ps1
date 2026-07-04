$python = "C:\Users\Shinn\Desktop\Koroki\.venv_diffsinger\Scripts\python.exe"
$trainDir = "C:\Users\Shinn\Desktop\Koroki\experiments\diffsinger\DiffSinger"
$config = "configs/koroki_yoasobi_phase1.yaml"
$maxRestarts = 30

function Stop-TrainingWorkers {
    $victims = Get-Process -Name "python" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $python }
    foreach ($p in $victims) {
        try { $p.Kill(); $p.WaitForExit(3000) } catch {}
    }
}

Push-Location $trainDir
try {
    for ($i = 1; $i -le $maxRestarts; $i++) {
        Write-Host "--- Phase 1 training run $i / $maxRestarts ---" -ForegroundColor Cyan
        & $python scripts/train.py acoustic --config $config --exp koroki_yoasobi_phase1
        $exit = $LASTEXITCODE
        if ($exit -eq 0) {
            Write-Host "Phase 1 training completed." -ForegroundColor Green
            break
        }
        Write-Host "Process exited with code $exit — cleaning up, restarting in 5s..." -ForegroundColor Yellow
        Stop-TrainingWorkers
        Start-Sleep -Seconds 5
    }
} finally {
    Pop-Location
}
