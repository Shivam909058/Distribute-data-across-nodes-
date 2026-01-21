@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

echo.
echo ╔═══════════════════════════════════════════════════════════╗
echo ║           VISHWARUPA - Distributed Storage                ║
echo ║                                                           ║
echo ║   Works on: Phone, Laptop, Tablet, Server - Any Device!  ║
echo ╚═══════════════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"
echo Working directory: %CD%

REM Get local IP
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    for /f "tokens=1" %%b in ("%%a") do (
        if not defined LOCAL_IP set "LOCAL_IP=%%b"
    )
)
REM Trim spaces
set LOCAL_IP=%LOCAL_IP: =%

if "%LOCAL_IP%"=="" (
    echo Could not detect IP. Check ipconfig.
    set /p LOCAL_IP="Enter this device's IP address: "
)

echo This device's IP: %LOCAL_IP%
echo.

echo ═══════════════════════════════════════════════════════════
echo   Choose how to start:
echo.
echo   [1] START NEW NETWORK (First device / Hub)
echo       - This device will host the web UI
echo       - Other devices will connect to this one
echo.
echo   [2] JOIN EXISTING NETWORK
echo       - Connect to another device already running
echo       - You'll need that device's IP address
echo.
echo ═══════════════════════════════════════════════════════════
set /p MODE_CHOICE="Enter choice (1 or 2): "

if "%MODE_CHOICE%"=="1" goto START_HUB
if "%MODE_CHOICE%"=="2" goto JOIN_NETWORK

echo Invalid choice. Defaulting to [1]...
goto START_HUB

:START_HUB
echo.
echo Starting as NETWORK HUB...

set SERVER_URL=http://127.0.0.1:8000
set LISTEN_PORT=9000

REM Check if agent exists
if exist "target\release\vishwarupa.exe" (
    set AGENT=target\release\vishwarupa.exe
) else (
    echo Building agent (first time only)...
    cargo build --release
    set AGENT=target\release\vishwarupa.exe
)

echo.
echo Starting web server in new window...
start "Vishwarupa Server" cmd /c "python server.py"

timeout /t 3 /nobreak >nul

echo Starting agent in new window...
start "Vishwarupa Agent" cmd /k "%AGENT%"

timeout /t 2 /nobreak >nul

echo.
echo ╔═══════════════════════════════════════════════════════════╗
echo ║              VISHWARUPA IS RUNNING                        ║
echo ╠═══════════════════════════════════════════════════════════╣
echo ║                                                           ║
echo ║  Open this URL on ANY device:                             ║
echo ║                                                           ║
echo ║     http://%LOCAL_IP%:8000
echo ║                                                           ║
echo ║  To add more devices, run on each device:                 ║
echo ║     ./start.sh or start.bat                               ║
echo ║     Choose [2] then enter: %LOCAL_IP%
echo ║                                                           ║
echo ║  Features: Upload, Download, Stream Video, Share          ║
echo ║                                                           ║
echo ╚═══════════════════════════════════════════════════════════╝
echo.
echo Close this window to stop, or close the Server/Agent windows.
pause
goto END

:JOIN_NETWORK
echo.
set /p HUB_IP="Enter the IP of the device running Vishwarupa: "

if "%HUB_IP%"=="" (
    echo Hub IP is required!
    pause
    exit /b 1
)

echo Testing connection to http://%HUB_IP%:8000...
curl -s --connect-timeout 5 "http://%HUB_IP%:8000/" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo Connected!
) else (
    echo Warning: Could not reach http://%HUB_IP%:8000
    echo Make sure the hub device is running and on the same network.
    set /p CONTINUE="Continue anyway? (y/n): "
    if /i not "!CONTINUE!"=="y" exit /b 1
)

set SERVER_URL=http://%HUB_IP%:8000
set LISTEN_PORT=9000

REM Check if agent exists
if exist "target\release\vishwarupa.exe" (
    set AGENT=target\release\vishwarupa.exe
) else (
    echo Building agent (first time only)...
    cargo build --release
    set AGENT=target\release\vishwarupa.exe
)

echo.
echo Starting agent...
start "Vishwarupa Agent" cmd /k "set LOCAL_IP=%LOCAL_IP%&& set SERVER_URL=%SERVER_URL%&& set LISTEN_PORT=9000&& %AGENT%"

timeout /t 2 /nobreak >nul

echo.
echo ╔═══════════════════════════════════════════════════════════╗
echo ║              JOINED THE NETWORK                           ║
echo ╠═══════════════════════════════════════════════════════════╣
echo ║                                                           ║
echo ║  Open this URL on ANY device:                             ║
echo ║                                                           ║
echo ║     http://%HUB_IP%:8000
echo ║                                                           ║
echo ║  This device is now storing shards                        ║
echo ║  Files uploaded will be distributed here too              ║
echo ║                                                           ║
echo ╚═══════════════════════════════════════════════════════════╝
echo.
pause

:END
endlocal
