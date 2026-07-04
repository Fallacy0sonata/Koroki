@echo off
cd /d C:\Users\Shinn\Desktop\Koroki
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\cleanup_port_9000.ps1" > "cowork_tests\kill9000_out.txt" 2>&1
timeout /t 3 /nobreak > nul
start "IndexTTS 9000 (cowork restart)" cmd /c ""C:\Users\Shinn\Desktop\Koroki\cowork_tests\run_indextts.bat""
exit
