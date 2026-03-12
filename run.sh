#!/usr/bin/env bash
# run.sh — Smart Money Tracker launcher (Linux / macOS)
# Usage:
#   ./run.sh              # API + Discord bot
#   ./run.sh --api-only   # API only
#   ./run.sh --telegram   # API + Telegram bot

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: Virtual environment not found."
    echo "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# Free port 8000 if already in use
if lsof -ti:8000 >/dev/null 2>&1; then
    echo "Port 8000 in use — stopping previous process..."
    kill "$(lsof -ti:8000)" 2>/dev/null || true
    sleep 0.6
fi

echo "Using: $VENV_PYTHON"
exec "$VENV_PYTHON" start.py "$@"
