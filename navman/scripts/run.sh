#!/bin/bash
# NavMan bot daemon launcher
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$SKILL_DIR/.venv"

# Load .env
if [ -f "$SKILL_DIR/.env" ]; then
    set -a
    source "$SKILL_DIR/.env"
    set +a
fi

# Create virtualenv if needed
if [ ! -d "$VENV" ]; then
    echo "[navman] Creating virtualenv..."
    python3 -m venv "$VENV"
fi

# Install dependencies
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$SKILL_DIR/requirements.txt"

mkdir -p "$SKILL_DIR/logs" "$SKILL_DIR/data/uploads" "$SKILL_DIR/data/exports"

echo "[navman] Starting bot..."
exec "$VENV/bin/python" "$SKILL_DIR/scripts/bot_handler.py" "$@"
