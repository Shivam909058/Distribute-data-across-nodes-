@echo off
echo ================================================
echo   VISHWARUPA SERVER (Run this on LAPTOP)
echo ================================================
echo.

cd %~dp0

REM Get laptop IP
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    for /f "tokens=1" %%b in ("%%a") do set LAPTOP_IP=%%b
)
echo Your laptop IP: %LAPTOP_IP%
echo.
echo After starting, access from any device at:
echo   http://%LAPTOP_IP%:8000
echo.
echo Starting server...
python server.py
