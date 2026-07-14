@echo off
rem Koroki Stage - one click: MediaMTX (diegetic screen relay) + stage server.
rem Then: OBS adds a Browser Source -> http://127.0.0.1:9770 (transparent).
rem To feed her monitor: OBS Settings > Stream > WHIP, server
rem   http://127.0.0.1:8889/koroki/whip  (no bearer token) > Start Streaming.
cd /d "%~dp0..\.."
start "MediaMTX (stage relay)" tools\mediamtx\mediamtx.exe tools\mediamtx\mediamtx.yml
start "Koroki Stage :9770" .venv\Scripts\python.exe -m http.server 9770 --directory clients\stage
timeout /t 2 >nul
start http://127.0.0.1:9770
echo Stage up: http://127.0.0.1:9770  (press F on the page for fps)
pause
