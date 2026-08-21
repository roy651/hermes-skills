#!/usr/bin/env bash
# Weekly upstream check + trial merge. READ-ONLY with respect to the live
# install: the merge happens in a scratch worktree that is deleted afterwards.
#
# Silent when already up to date (empty stdout => the --no-agent cron job sends
# nothing). Otherwise prints a short report ending with the deploy command.
#
# Why trial-merge rather than just count commits: the old job reported "N
# commits behind" every week and nobody acted, so it drifted to 2,918. A report
# that says "clean, say go" is actionable; a number is not. The merge is the
# tedious-but-safe half, so automate it; the DEPLOY restarts everything on the
# box, so that stays manual.
set -uo pipefail

AGENT="$HOME/.hermes/hermes-agent"
WT="/tmp/hermes-upstream-check-$$"
BRANCH="upstream-check-$$"

cd "$AGENT" || { echo "hermes-agent checkout not found at $AGENT"; exit 1; }

git fetch --quiet origin 2>/dev/null

BEHIND=$(git rev-list --count HEAD..origin/main 2>/dev/null)
[ -z "$BEHIND" ] && { echo "upstream check FAILED: could not compare against origin/main"; exit 1; }
[ "$BEHIND" -eq 0 ] && exit 0        # up to date -> silent

LOCAL_AHEAD=$(git rev-list --count origin/main..HEAD 2>/dev/null)

cleanup () {
  git worktree remove --force "$WT" >/dev/null 2>&1
  git branch -D "$BRANCH" >/dev/null 2>&1
}
trap cleanup EXIT

if ! git worktree add -b "$BRANCH" "$WT" HEAD >/dev/null 2>&1; then
  echo "⚠️ Hermes upstream: ${BEHIND} commits behind, but the trial worktree could not be created."
  exit 1
fi

cd "$WT" || exit 1
if git merge --no-edit origin/main >/dev/null 2>&1; then
  VERDICT="clean"
  CONFLICTS=""
else
  VERDICT="conflicts"
  CONFLICTS=$(git diff --name-only --diff-filter=U | head -12)
  git merge --abort >/dev/null 2>&1
fi
cd "$AGENT" || exit 1

echo "🔄 Hermes upstream — ${BEHIND} commits behind"
echo
echo "Local commits carried: ${LOCAL_AHEAD}"
echo "Trial merge: ${VERDICT}"
echo

# Security-relevant commits first -- these are why the cadence matters at all.
SEC=$(git log --oneline HEAD..origin/main 2>/dev/null \
      | grep -iE "CVE-|GHSA-|security|vulnerab" | head -6)
if [ -n "$SEC" ]; then
  echo "🔐 Security-related upstream commits:"
  echo '```'
  echo "$SEC"
  echo '```'
  echo
fi

NOTABLE=$(git log --oneline HEAD..origin/main 2>/dev/null \
          | grep -iE "^[0-9a-f]+ feat" | head -8)
if [ -n "$NOTABLE" ]; then
  echo "✨ Notable features:"
  echo '```'
  echo "$NOTABLE"
  echo '```'
  echo
fi

if [ "$VERDICT" = "clean" ]; then
  cat <<TXT
✅ Merges cleanly. To deploy (restarts the gateway — a few minutes):

\`\`\`
hermes update --backup --yes
systemctl --user restart claude-proxy
\`\`\`

Then verify with the monthly audit:
\`\`\`
bash ~/.hermes/scripts/minipc_audit.sh
\`\`\`
TXT
else
  echo "🔴 Conflicts — needs a decision. Files:"
  echo '```'
  echo "$CONFLICTS"
  echo '```'
  echo
  echo "These land on the local reverts (voice transcript, cron retry, cron"
  echo "delivery order): upstream changed code you deliberately reverted, so each"
  echo "one is 'keep the revert' or 'adopt upstream' — a judgement call, not a"
  echo "mechanical fix."
  echo
  echo "To get a neutral read on each file first:"
  echo '```'
  echo "Use the merge-reconciler skill on the hermes-agent upstream merge —"
  echo "for each conflicted file, summarise what upstream changed, what our"
  echo "revert was protecting, and whether the revert is still needed."
  echo '```'
  echo
  echo "Full procedure: hermes-upstream-sync/SKILL.md"
fi
