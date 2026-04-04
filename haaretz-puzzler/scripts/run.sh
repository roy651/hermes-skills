#!/bin/bash
# Entry point for Hermes haaretz-puzzler skill.
# Calls the cache manager, which decides whether to fetch from Haaretz or serve cached.
set -e

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$SKILL_DIR/.venv"

# Load .env if present (don't override existing env vars)
if [ -f "$SKILL_DIR/.env" ]; then
    set -a
    source "$SKILL_DIR/.env"
    set +a
fi

if [ ! -d "$VENV" ]; then
    echo "[setup] Creating virtual environment..." >&2
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install playwright aiohttp --quiet 2>&1 | tail -1 >&2
    "$VENV/bin/playwright" install chromium 2>&1 | tail -2 >&2
fi

# Ensure output directory exists
mkdir -p "$SKILL_DIR/output"

# Call the cache manager (which internally calls the browser script if needed)
exec "$VENV/bin/python" "$SKILL_DIR/scripts/puzzle_cache.py" "$@"
