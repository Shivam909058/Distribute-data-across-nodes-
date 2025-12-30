@echo off
REM Vishwarupa Quick Start Script for Windows

echo ========================================
echo Vishwarupa - Decentralized Personal Storage
echo ========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [X] Python 3 is not installed. Please install Python 3.11+
    exit /b 1
)

REM Check if Rust is installed
cargo --version >nul 2>&1
if errorlevel 1 (
    echo [X] Rust is not installed. Install from https://rustup.rs
    exit /b 1
)

echo [✓] Python found
echo [✓] Rust found
echo.

REM Install Python dependencies
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

echo Installing Python dependencies...
call venv\Scripts\activate.bat
pip install -q -r requirements.txt

REM Build Rust agent
echo.
echo Building agent...
cargo build --release

if errorlevel 1 (
    echo [X] Build failed
    exit /b 1
)

echo.
echo [✓] Build complete!
echo.
echo ========================================
echo Next Steps:
echo ========================================
echo.
echo 1. Start the controller (in Command Prompt 1):
echo    venv\Scripts\uvicorn server:app --host 0.0.0.0 --port 8000
echo.
echo 2. Open browser:
echo    http://localhost:8000
echo.
echo 3. Run agent daemon (in Command Prompt 2):
echo    target\release\agent.exe
echo.
echo 4. Run agents on other devices, then upload:
echo    target\release\agent.exe upload myfile.pdf
echo.
echo 5. Download on any device:
echo    target\release\agent.exe download [file_id] output.pdf
echo.
echo ========================================

pause

