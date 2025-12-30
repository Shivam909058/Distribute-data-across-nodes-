@echo off
if "%~1"=="" (
    echo Usage: download.bat ^<file_id^> ^<output_file^>
    exit /b 1
)

set LISTEN_PORT=9999
target\release\vishwarupa.exe download %1 %2

