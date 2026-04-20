#!/bin/bash
set -euo pipefail

IMPORTER_DIR="$(cd "$(dirname "$0")" && pwd)"
MONEYMAN_DIR="$IMPORTER_DIR/moneyman-repo"
ENCRYPTED_CONFIG="$IMPORTER_DIR/config.enc"
AGE_KEY="${AGE_KEY_FILE:-$HOME/.finance-key}"

if [ ! -f "$ENCRYPTED_CONFIG" ]; then
    echo "[import] ERROR: $ENCRYPTED_CONFIG not found. Run setup first." >&2
    exit 1
fi

if [ ! -f "$AGE_KEY" ]; then
    echo "[import] ERROR: age key not found at $AGE_KEY" >&2
    exit 1
fi

TMPCONFIG="$(mktemp /tmp/moneyman-config.XXXXXX.json)"
trap 'rm -f "$TMPCONFIG"' EXIT

age --decrypt -i "$AGE_KEY" "$ENCRYPTED_CONFIG" > "$TMPCONFIG"

MONEYMAN_CONFIG_PATH="$TMPCONFIG" node "$MONEYMAN_DIR/dst/index.js"

echo "[import] Credit card import completed at $(date)"
