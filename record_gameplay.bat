@echo off
REM Limbs Stage 0 - record gameplay as training corpus.
REM Usage: record_gameplay.bat [game_slug]   (start the game FIRST)
REM F10 or Ctrl+C stops. Session auto-archives to Koroki Storage on G:.
cd /d "%~dp0"
if "%~1"=="" (
  .venv\Scripts\python.exe demo_recorder.py --window Roblox
) else (
  .venv\Scripts\python.exe demo_recorder.py --window Roblox --game %1
)
pause
