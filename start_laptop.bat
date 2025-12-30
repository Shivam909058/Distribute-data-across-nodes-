@echo off
REM Vishwarupa - Laptop Startup Script

echo ================================
echo   Vishwarupa - Laptop
echo ================================
echo.

REM Create master keys if don't exist
if not exist master_9000.key (
    echo Creating master keys...
    echo 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef> master_9000.key
    echo 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef> master_9001.key
    echo 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef> master_9999.key
)

echo Starting web server on port 8000...
start /B python server.py
timeout /t 2 /nobreak > nul

echo Starting agent 1 on port 9000...
set LISTEN_PORT=9000
start /B target\release\vishwarupa.exe
timeout /t 2 /nobreak > nul

echo Starting agent 2 on port 9001...
set LISTEN_PORT=9001
start /B target\release\vishwarupa.exe
timeout /t 2 /nobreak > nul

echo.
echo ================================
echo   Vishwarupa is running!
echo ================================
echo.
echo Open browser and go to:
echo   http://localhost:8000
echo.
echo Or from phone/tablet on same network:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4 Address"') do (
    echo   http://%%a:8000
)
echo.
echo Press any key to stop all services...
pause > nul

taskkill /f /im python.exe /fi "WINDOWTITLE eq server.py" 2>nul
taskkill /f /im vishwarupa.exe 2>nul
echo.
echo Stopped.

