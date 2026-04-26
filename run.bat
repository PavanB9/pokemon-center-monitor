@echo off
cd /d "%~dp0"

IF NOT EXIST "venv\Scripts\pythonw.exe" (
    echo [Pokemon Center Monitor] First run detected. Setting up virtual environment...
    python -m venv venv
    echo [Pokemon Center Monitor] Installing dependencies...
    venv\Scripts\pip.exe install -r requirements.txt
    echo [Pokemon Center Monitor] Setup complete! Starting app...
)

start "" venv\Scripts\pythonw.exe monitor.py
