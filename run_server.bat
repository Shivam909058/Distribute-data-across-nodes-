@echo off
cd %~dp0
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload

