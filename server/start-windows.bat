@echo off
REM Start the Scenic API and its Cloudflare tunnel on Windows.
REM
REM Double-click this, or point a Task Scheduler task at it to run at startup.
REM Two console windows open and must stay open — closing one stops that half.
REM
REM Paths are derived from this script's own location (%~dp0 is the server\
REM folder), so the repo can live anywhere without editing this file.

set ROOT=%~dp0..

REM Bind to loopback only: cloudflared reaches the app over localhost, so nothing
REM off-box needs to connect. Keeps the LAN out and avoids the firewall prompt.
set SCENIC_HOST=127.0.0.1

start "Scenic API" "%ROOT%\.venv\Scripts\python.exe" "%ROOT%\server\serve.py"
start "Scenic Tunnel" cloudflared.exe tunnel run scenic

echo.
echo Started two windows: Scenic API and Scenic Tunnel.
echo The API takes a few seconds to load the graph before it answers.
echo.
echo Verify with:  curl.exe https://api.jameskouvlis.com/api/health
echo.
