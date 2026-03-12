# run.ps1 - Smart Money Tracker launcher
# Usage:
#   .\run.ps1              # API + Discord bot
#   .\run.ps1 --api-only   # API only
#   .\run.ps1 --telegram   # API + Telegram bot

Set-StrictMode -Off
$ErrorActionPreference = "Stop"

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Error "Virtual environment not found. Run: python -m venv .venv && .venv\Scripts\pip install -r requirements.txt"
    exit 1
}

# Free port 8000 if already in use
$occupied = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
if ($occupied) {
    Write-Host "Port 8000 in use -- stopping previous process (PID $($occupied.OwningProcess))..." -ForegroundColor Yellow
    Stop-Process -Id $occupied.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 600
}

Write-Host "Using: $venvPython" -ForegroundColor Cyan
& $venvPython start.py @args
