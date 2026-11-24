@echo off
setlocal
cd /d "%~dp0"

echo =========================================================
echo    Starting Local Video Studio Pro (Port 5050)...
echo =========================================================
echo.

start "" http://127.0.0.1:5050
python server.py

pause
