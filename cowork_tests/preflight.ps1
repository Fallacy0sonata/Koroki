Set-Location C:\Users\Shinn\Desktop\Koroki
"=== ComfyUI kill ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {$_.CommandLine -like '*ComfyUI*'} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "killed PID $($_.ProcessId)" }
"=== VRAM ==="
nvidia-smi --query-gpu=memory.used --format=csv
"=== doctor ==="
& .\scripts\doctor.ps1
"=== DONE preflight $(Get-Date) ==="
