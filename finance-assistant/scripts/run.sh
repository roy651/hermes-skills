#!/bin/bash
# Finance Assistant bot daemon launcher
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$SKILL_DIR/.venv"

if [ -f "$SKILL_DIR/.env" ]; then
    set -a
    source "$SKILL_DIR/.env"
    set +a
fi

if [ ! -d "$VENV" ]; then
    echo "[finance-assistant] Creating virtualenv..."
    python3 -m venv "$VENV"
fi

if ! "$VENV/bin/python" -c "import requests, actual" 2>/dev/null; then
    echo "[finance-assistant] Installing dependencies..."
    "$VENV/bin/pip" install --quiet --timeout 120 -r "$SKILL_DIR/requirements.txt"
else
    echo "[finance-assistant] Dependencies already installed."
fi

mkdir -p "$SKILL_DIR/logs" "$SKILL_DIR/data"

echo "[finance-assistant] Starting bot..."
exec "$VENV/bin/python" "$SKILL_DIR/scripts/bot_handler.py" "$@"
