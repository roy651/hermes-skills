#!/bin/bash
# Quick helper to sync wyckoff skill to external repo
# Usage: ./sync-wyckoff.sh

set -e

SKILL_DIR="$HOME/.hermes/skills/wyckoff"
GIT_DIR="$HOME/hermes-skills/wyckoff"

echo "=== Syncing wyckoff skill to external repo ==="

# Pull latest from remote
cd "$GIT_DIR"
git pull origin main

# Copy modified files back to runtime dir (preserving .venv, .env, logs)
cp "$SKILL_DIR/scripts/data.py" "$GIT_DIR/scripts/"
cp "$SKILL_DIR/scripts/exit.py" "$GIT_DIR/scripts/"
cp "$SKILL_DIR/scripts/verify_yahoo_limits.py" "$GIT_DIR/scripts/"
cp "$SKILL_DIR/references/yahoo-finance-rate-limit.md" "$GIT_DIR/references/"

# Commit and push
cd "$GIT_DIR"
git add scripts/ references/
git commit -m "wyckoff: Yahoo Finance backoff fixes + verification script"
git push origin main

echo "✅ Sync complete!"
