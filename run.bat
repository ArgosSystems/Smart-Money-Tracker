@echo off
:: run.bat — Smart Money Tracker launcher for Command Prompt
:: Usage:
::   run.bat              (API + Discord bot)
::   run.bat --api-only
::   run.bat --telegram

set VENV_PYTHON=%~dp0.venv\Scripts\python.exe

if not exist "%VENV_PYTHON%" (
    echo ERROR: Virtual environment not found.
    echo Run: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)

echo Using: %VENV_PYTHON%
"%VENV_PYTHON%" start.py %*
