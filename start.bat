@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

echo.
echo ========================================================
echo           VISHWARUPA - Distributed Storage
echo.
echo   Works on: Phone, Laptop, Tablet, Server - Any Device
echo ========================================================
echo.

cd /d "%~dp0"
echo Working directory: %CD%

REM Get local IP - find first IPv4 address
set "LOCAL_IP="
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    if not defined LOCAL_IP (
        set "LOCAL_IP=%%a"
    )
)
REM Trim leading space
if defined LOCAL_IP set "LOCAL_IP=!LOCAL_IP:~1!"

if not defined LOCAL_IP (
    echo Could not detect IP. Check ipconfig.
    set /p "LOCAL_IP=Enter this device's IP address: "
)

echo This device's IP: %LOCAL_IP%
echo.

echo ========================================================
echo   Choose how to start:
echo.
echo   [1] START NEW NETWORK - First device / Hub
echo   [2] JOIN EXISTING NETWORK - Connect to another device
echo.
echo ========================================================
set /p "MODE_CHOICE=Enter choice (1 or 2): "

if "%MODE_CHOICE%"=="2" goto JOIN_NETWORK

:START_HUB
echo.
echo Starting as NETWORK HUB...
echo.

set "SERVER_URL=http://127.0.0.1:8000"
set "LISTEN_PORT=9000"

REM Check if agent exists
set "AGENT=target\release\vishwarupa.exe"
if not exist "%AGENT%" (
    echo Building agent - first time only, please wait...
    cargo build --release
)

echo Starting web server...
start "Vishwarupa-Server" cmd /c "python server.py"

echo Waiting for server to start...
timeout /t 3 /nobreak >nul

echo Starting agent...
start "Vishwarupa-Agent" cmd /k "set LISTEN_PORT=9000 && %AGENT%"

timeout /t 2 /nobreak >nul

echo.
echo ========================================================
echo              VISHWARUPA IS RUNNING
echo ========================================================
echo.
echo   Open this URL on ANY device:
echo.
echo      http://%LOCAL_IP%:8000
echo.
echo   To add more devices:
echo      Run start.bat or ./start.sh on the other device
echo      Choose [2] and enter: %LOCAL_IP%
echo.
echo   Features: Upload, Download, Stream Video, Share
echo.
echo ========================================================
echo.
echo Close the Server and Agent windows to stop.
pause
goto END

:JOIN_NETWORK
echo.
set /p "HUB_IP=Enter the IP of the hub device: "

if "%HUB_IP%"=="" (
    echo Hub IP is required!
    pause
    goto END
)

echo.
echo Connecting to http://%HUB_IP%:8000...

set "SERVER_URL=http://%HUB_IP%:8000"
set "LISTEN_PORT=9000"

REM Check if agent exists
set "AGENT=target\release\vishwarupa.exe"
if not exist "%AGENT%" (
    echo Building agent - first time only, please wait...
    cargo build --release
)

echo Starting agent...
start "Vishwarupa-Agent" cmd /k "set LOCAL_IP=%LOCAL_IP% && set SERVER_URL=%SERVER_URL% && set LISTEN_PORT=9000 && %AGENT%"

timeout /t 2 /nobreak >nul

echo.
echo ========================================================
echo              JOINED THE NETWORK
echo ========================================================
echo.
echo   Open this URL on ANY device:
echo.
echo      http://%HUB_IP%:8000
echo.
echo   This device is now storing shards.
echo   Files uploaded will be distributed here too.
echo.
echo ========================================================
echo.
pause

:END
endlocal
