@echo off
REM Record a Minecraft session (your window + inputs). F10 or Ctrl+C stops.
REM Usage: record_minecraft.bat   (start Minecraft FIRST, then run this)
cd /d "%~dp0"
.venv\Scripts\python.exe demo_recorder.py --window Minecraft --game minecraft --fps 15
pause
