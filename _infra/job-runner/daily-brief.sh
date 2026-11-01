#!/usr/bin/env bash
# The brief sends itself through wyckoff's notifier, so stdout here carries only problems:
# a section that failed while the rest of the brief still went out.

cd "$HOME/hermes-skills/wyckoff" || exit 1

log=$(mktemp)
trap 'rm -f "$log"' EXIT

.venv/bin/python scripts/digest.py --daily >"$log" 2>&1
status=$?

cat "$log" >&2
grep -iE "traceback|section .* failed" "$log" | head -3

exit "$status"
