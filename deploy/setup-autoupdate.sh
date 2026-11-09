#!/usr/bin/env bash
# One-time setup: link /opt/rbs-checker to GitHub and turn on automatic updates.
# On the server run:
#   curl -fsSL https://raw.githubusercontent.com/Qayzan/rbs-checker/main/deploy/setup-autoupdate.sh | sudo bash
set -euo pipefail
APP=/opt/rbs-checker
REPO=https://github.com/Qayzan/rbs-checker.git
if [ "$(id -u)" -ne 0 ]; then echo 'Run with sudo.'; exit 1; fi
[ -d "$APP/.venv" ] || { echo "$APP is not installed yet; run deploy/install.sh first."; exit 1; }

command -v git >/dev/null || { apt-get update -qq; apt-get install -y -qq git; }
cd "$APP"
if [ ! -d .git ]; then
  git init --quiet
  git remote add origin "$REPO"
fi
git fetch --quiet origin main || {
  echo "Could not download $REPO. If the repository is private, see deploy/README.md."; exit 1; }
# First link: take GitHub's version as the starting point. Saved logins, the
# token, the Python environment and the browser are outside Git and stay as they are.
git reset --quiet --hard origin/main
git checkout --quiet -B main origin/main

for unit in rbs-checker.service rbs-checker-update.service rbs-checker-update.timer; do
  install -m 644 "deploy/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload

TESTDATA=$(mktemp -d)
if env -u BOT_TOKEN RBS_DATA_DIR="$TESTDATA" .venv/bin/python -m unittest discover -s tests >/tmp/rbs-tests.log 2>&1; then
  echo "Tests passed."
else
  echo "Tests FAILED, see /tmp/rbs-tests.log. The bot was not restarted."; rm -rf "$TESTDATA"; exit 1
fi
rm -rf "$TESTDATA"

systemctl restart rbs-checker
systemctl enable --now rbs-checker-update.timer
echo
echo "Done. The bot now runs $(git log -1 --format='%h %s')"
echo "From now on it updates itself within ~2 minutes of every push to GitHub."
echo "Update log: sudo journalctl -u rbs-checker-update -n 30"
