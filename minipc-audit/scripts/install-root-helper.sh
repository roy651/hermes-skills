#!/usr/bin/env bash
# Install the privileged half of the audit. Run with sudo after every git pull
# that changes minipc-audit-root.sh.
#
# The installed copy MUST be root-owned and outside the git checkout. It is
# invoked through a NOPASSWD sudoers rule, and a NOPASSWD rule pointing at a
# file the invoking user can write is simply a root shell — the user edits the
# script and runs it. /usr/local/sbin is root:root drwxr-xr-x, so roy650 can
# neither modify this file nor create a rival one beside it.
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)/minipc-audit-root.sh"
DST=/usr/local/sbin/minipc-audit-root

[ "$(id -u)" -eq 0 ] || { echo "run me with sudo"; exit 1; }
[ -f "$SRC" ] || { echo "source missing: $SRC"; exit 1; }

install -o root -g root -m 0755 "$SRC" "$DST"
echo "installed $DST"
ls -l "$DST"

echo
echo "sudoers rule that authorises it:"
grep -h "minipc-audit-root" /etc/sudoers.d/* 2>/dev/null | sed 's/^/  /' \
  || echo "  !! no sudoers rule found — the audit will mark privileged checks UNKNOWN"

echo
echo "smoke test (should print JSON):"
sudo -n "$DST" 2>&1 | head -c 200; echo
