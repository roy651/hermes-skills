#!/usr/bin/env bash
set -e

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$SKILL_DIR/.venv"

if [ -f "$SKILL_DIR/.env" ]; then
    set -a; source "$SKILL_DIR/.env"; set +a
fi

if [ ! -d "$VENV" ]; then
    echo "[setup] Creating virtual environment..." >&2
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q -r "$SKILL_DIR/requirements.txt"
fi

mkdir -p "$SKILL_DIR/data" "$SKILL_DIR/logs"

exec "$VENV/bin/python" "$SKILL_DIR/scripts/bot.py"
