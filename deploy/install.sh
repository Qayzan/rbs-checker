#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  echo 'Run with sudo bash deploy/install.sh'; exit 1
fi
cd "$(dirname "$0")/.."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv python3-pip ca-certificates
id rbsbot >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/rbs-checker --shell /usr/sbin/nologin rbsbot
install -d -o rbsbot -g rbsbot -m 700 /var/lib/rbs-checker
python3 -m venv .venv
.venv/bin/python -m pip install -r deploy/requirements.txt
.venv/bin/playwright install-deps chromium
install -d -o rbsbot -g rbsbot -m 755 /opt/rbs-checker/browsers
runuser -u rbsbot -- env PLAYWRIGHT_BROWSERS_PATH=/opt/rbs-checker/browsers /opt/rbs-checker/.venv/bin/playwright install chromium
if [ ! -e /etc/rbs-checker.env ]; then
  install -m 600 /dev/null /etc/rbs-checker.env
fi
install -m 644 deploy/rbs-checker.service /etc/systemd/system/rbs-checker.service
systemctl daemon-reload
systemctl enable rbs-checker
echo 'Installed. Service is not started: configure token and migrate data before cutover.'
