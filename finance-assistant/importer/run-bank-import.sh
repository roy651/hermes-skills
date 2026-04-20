#!/bin/bash
# Bank Leumi import — password NEVER stored on disk.
# Reads LEUMI_PASSWORD from environment (set by bot) or prompts interactively.
set -euo pipefail

IMPORTER_DIR="$(cd "$(dirname "$0")" && pwd)"
MONEYMAN_DIR="$IMPORTER_DIR/moneyman-repo"
ENCRYPTED_CONFIG="$IMPORTER_DIR/config.enc"
AGE_KEY="${AGE_KEY_FILE:-$HOME/.finance-key}"

if [ -z "${LEUMI_PASSWORD:-}" ]; then
    read -rs -p "Bank Leumi password: " LEUMI_PASSWORD
    echo
fi

if [ -z "$LEUMI_PASSWORD" ]; then
    echo "[bank-import] ERROR: no password provided." >&2
    exit 1
fi

if [ ! -f "$ENCRYPTED_CONFIG" ]; then
    echo "[bank-import] ERROR: $ENCRYPTED_CONFIG not found." >&2
    exit 1
fi

TMPCONFIG="$(mktemp /tmp/moneyman-leumi.XXXXXX.json)"
trap 'rm -f "$TMPCONFIG"' EXIT

# Decrypt main config to extract storage/options, then build a Leumi-only config
age --decrypt -i "$AGE_KEY" "$ENCRYPTED_CONFIG" | \
    node -e "
const fs = require('fs');
const main = JSON.parse(fs.readFileSync('/dev/stdin', 'utf8'));
const leumi = {
  accounts: [{
    companyId: 'leumi',
    username: process.env.LEUMI_ID,
    password: process.env.LEUMI_PASSWORD
  }],
  storage: main.storage,
  options: main.options
};
fs.writeFileSync(process.env.TMPCONFIG, JSON.stringify(leumi));
" LEUMI_ID="${LEUMI_ID:-}" LEUMI_PASSWORD="$LEUMI_PASSWORD" TMPCONFIG="$TMPCONFIG"

MONEYMAN_CONFIG_PATH="$TMPCONFIG" node "$MONEYMAN_DIR/dst/index.js"

unset LEUMI_PASSWORD

echo "[bank-import] Bank Leumi import completed at $(date)"
