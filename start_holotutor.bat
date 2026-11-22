@echo off
rem Starts the HoloTutor-X server and opens it in the browser.
rem Double-click this file. Close this window (or press Ctrl+C) to stop the server.

cd /d "%~dp0backend"

if not exist "..\.venv\Scripts\python.exe" (
    echo The .venv folder was not found. Create it first:  python -m venv .venv
    pause
    exit /b 1
)

echo Starting HoloTutor-X ... the browser opens in about 20 seconds.
start "" /b cmd /c "timeout /t 20 /nobreak >nul & start http://localhost:8000"
"..\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

pause
