#!/bin/bash
# Entry point for Hermes. Ensures the venv exists and dependencies are installed,
# then runs the renewal script, forwarding all arguments.
set -e

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$SKILL_DIR/.venv"

if [ ! -d "$VENV" ]; then
    echo "[setup] Creating virtual environment..." >&2
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -r "$SKILL_DIR/requirements.txt" --quiet
fi

exec "$VENV/bin/python" "$SKILL_DIR/scripts/renew-reolink.py" "$@"
