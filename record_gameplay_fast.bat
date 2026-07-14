@echo off
REM Limbs Stage 0 - record FAST/high-APM gameplay (20 fps for tighter action alignment).
REM Usage: record_gameplay_fast.bat [game_slug]   (start the game FIRST; F10 stops)
cd /d "%~dp0"
if "%~1"=="" (
  .venv\Scripts\python.exe demo_recorder.py --window Roblox --fps 20
) else (
  .venv\Scripts\python.exe demo_recorder.py --window Roblox --game %1 --fps 20
)
pause
