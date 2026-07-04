#!/usr/bin/env powershell
# Clean up any processes using port 9000 (IndexTTS adapter port)
# NOTE: $pid is a read-only automatic variable in PowerShell (this script's own PID).
# A previous version assigned to it, which failed silently and made the script kill itself.

$port = 9000
Write-Host "Checking for processes using port $port..." -ForegroundColor Yellow

$connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
$ownerPids = $connections | Select-Object -ExpandProperty OwningProcess -Unique

if ($ownerPids) {
    foreach ($ownerPid in $ownerPids) {
        $proc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "Found process on port $port : $($proc.ProcessName) (PID: $ownerPid)" -ForegroundColor Red
            Write-Host "Killing process..." -ForegroundColor Yellow
            Stop-Process -Id $ownerPid -Force
            Write-Host "Process $ownerPid killed." -ForegroundColor Green
        }
    }
} else {
    Write-Host "Port $port is free." -ForegroundColor Green
}
