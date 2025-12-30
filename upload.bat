@echo off
if "%~1"=="" (
    echo Usage: upload.bat ^<filename^>
    exit /b 1
)

set LISTEN_PORT=9999
target\release\vishwarupa.exe upload %1

