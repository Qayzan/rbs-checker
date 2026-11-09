# Google Cloud deployment

The bot runs as the `rbs-checker` system service on Ubuntu 24.04 (VM `rbs-bot`,
zone `us-central1-f`, project `symmetric-lore-454109-v1`, 1 GB swap file).

- Program: `/opt/rbs-checker` (a Git checkout of this repository)
- Token: `/etc/rbs-checker.env`, readable only by root
- Saved sessions and preferences: `/var/lib/rbs-checker`, readable only by the service user
- Logs: `sudo journalctl -u rbs-checker`
- Status / restart / stop: `sudo systemctl status|restart|stop rbs-checker`

## Automatic updates from GitHub

Push to `main` and the server picks it up within about 2 minutes:
`rbs-checker-update.timer` runs `deploy/update.sh`, which pulls the new commit,
installs packages if `deploy/requirements.txt` changed, runs the tests, and
restarts the bot. If the tests fail, the server stays on the previous version and
skips that commit until a newer one arrives.

- Update log: `sudo journalctl -u rbs-checker-update -n 30`
- Last test output: `sudo cat /var/lib/rbs-checker-update/last-test.log`
- Update right now: `sudo bash /opt/rbs-checker/deploy/update.sh`
- One-time setup (already done once per server):
  `curl -fsSL https://raw.githubusercontent.com/Qayzan/rbs-checker/main/deploy/setup-autoupdate.sh | sudo bash`

If the repository is ever made private, the server needs a read-only deploy key:
`sudo ssh-keygen -t ed25519 -f /root/.ssh/rbs_deploy -N ""`, add the `.pub` file under
GitHub → Settings → Deploy keys, then
`sudo git -C /opt/rbs-checker remote set-url origin git@github.com:Qayzan/rbs-checker.git`
and add `IdentityFile /root/.ssh/rbs_deploy` for `github.com` in `/root/.ssh/config`.

## Memory settings (1 GB VM)

One search at a time, up to two warm browser pages, pages kept for 2 minutes and the
browser for 5 minutes after use, so back-to-back searches skip the slow start-up.
The service is capped at 800 MB; exceeding it restarts the bot.

## Laptop copy

`bot/cloud-active` on the laptop stops `start-bot.bat` from starting a second copy.
To roll back to the laptop: stop the cloud service first, delete that file, run the
launcher. Never run both copies with the same Telegram token.

Deployment archives are excluded from Git. Never commit `.env` files, saved sessions,
or migration archives.
