#!/usr/bin/env bash
# Pull new commits from GitHub, test them, and restart the bot.
# Runs every 2 minutes via rbs-checker-update.timer. Safe to run by hand:
#   sudo bash /opt/rbs-checker/deploy/update.sh
set -euo pipefail
APP=/opt/rbs-checker
BRANCH=main
STATE=/var/lib/rbs-checker-update
mkdir -p "$STATE"
cd "$APP"

exec 9>"$STATE/lock"
flock -n 9 || exit 0                       # an update is already running

git fetch --quiet origin "$BRANCH"
OLD=$(git rev-parse HEAD)
NEW=$(git rev-parse "origin/$BRANCH")
[ "$OLD" = "$NEW" ] && exit 0
if [ "$(cat "$STATE/failed" 2>/dev/null)" = "$NEW" ]; then
  exit 0                                   # this commit already failed its tests; wait for a fix
fi

echo "Updating $(git log -1 --format='%h' "$OLD") -> $(git log -1 --format='%h %s' "$NEW")"
git reset --quiet --hard "$NEW"

rollback() {
  echo "Update failed ($1). Staying on the previous version."
  echo "$NEW" > "$STATE/failed"
  git reset --quiet --hard "$OLD"
  exit 1
}

if ! git diff --quiet "$OLD" "$NEW" -- deploy/requirements.txt; then
  .venv/bin/python -m pip install --quiet -r deploy/requirements.txt || rollback "package install failed"
  runuser -u rbsbot -- env PLAYWRIGHT_BROWSERS_PATH="$APP/browsers" "$APP/.venv/bin/playwright" install chromium \
    || rollback "browser install failed"
fi

TESTDATA=$(mktemp -d)
env -u BOT_TOKEN RBS_DATA_DIR="$TESTDATA" .venv/bin/python -m unittest discover -s tests >"$STATE/last-test.log" 2>&1 \
  || { rm -rf "$TESTDATA"; rollback "tests failed, see $STATE/last-test.log"; }
rm -rf "$TESTDATA"

for unit in rbs-checker.service rbs-checker-update.service rbs-checker-update.timer; do
  if ! cmp -s "deploy/$unit" "/etc/systemd/system/$unit"; then
    install -m 644 "deploy/$unit" "/etc/systemd/system/$unit"
    CHANGED_UNITS=1
  fi
done
[ -n "${CHANGED_UNITS:-}" ] && systemctl daemon-reload

rm -f "$STATE/failed"
systemctl restart rbs-checker
echo "Bot restarted on $(git log -1 --format='%h %s')"
