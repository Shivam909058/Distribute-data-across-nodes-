@echo off
REM ============================================
REM VISHWARUPA - LAPTOP STARTUP SCRIPT
REM ============================================
REM Starts the controller server and agents
REM ============================================

echo ================================================
echo   VISHWARUPA - Decentralized Personal Storage
echo ================================================
echo.

REM Check if binary exists
if not exist target\release\vishwarupa.exe (
    echo ERROR: vishwarupa.exe not found!
    echo Please run: cargo build --release
    pause
    exit /b 1
)

REM Create master keys if don't exist
if not exist master_9000.key (
    echo Creating master keys...
    echo 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef> master_9000.key
    echo 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef> master_9001.key
    echo 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef> master_9999.key
    echo.
    echo IMPORTANT: For real use, delete these files and enter 
    echo            the same password on all devices!
    echo.
)

echo [1/3] Starting web server on port 8000...
start /B "" python server.py
timeout /t 3 /nobreak > nul

echo [2/3] Starting agent 1 on port 9000...
set LISTEN_PORT=9000
start /B "" target\release\vishwarupa.exe
timeout /t 2 /nobreak > nul

echo [3/3] Starting agent 2 on port 9001...
set LISTEN_PORT=9001
start /B "" target\release\vishwarupa.exe
timeout /t 2 /nobreak > nul

echo.
echo ================================================
echo   VISHWARUPA IS RUNNING!
echo ================================================
echo.
echo Open browser and go to:
echo   http://localhost:8000
echo.
echo From phone/tablet on same WiFi network:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4 Address"') do (
    set LAPTOP_IP=%%a
    echo   http://%%a:8000
)
echo.
echo ------------------------------------------------
echo  PHONE SETUP INSTRUCTIONS:
echo ------------------------------------------------
echo  On your phone (Termux/proot Ubuntu):
echo.
echo    export SERVER_URL=http://YOUR_LAPTOP_IP:8000
echo    ./start_phone.sh
echo.
echo  Replace YOUR_LAPTOP_IP with the IP shown above.
echo ------------------------------------------------
echo.
echo Press any key to stop all services...
pause > nul

echo.
echo Stopping services...
taskkill /f /im python.exe 2>nul
taskkill /f /im vishwarupa.exe 2>nul
echo.
echo Stopped.

