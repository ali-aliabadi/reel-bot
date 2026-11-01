#!/bin/sh
# Fast-forwards /opt/reel-bot to origin/master and syncs the virtualenv, so a merge
# reaches the VPS within 5 minutes (reel-bot-update.timer). Master only gets green PRs.
# A dirty or diverged checkout fails the unit instead of being overwritten.
set -eu
cd /opt/reel-bot
git fetch -q origin master
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/master)" ] && exit 0
# ponytail: skips while a tick runs; a tick starting mid-update still races, add a lock if that bites.
# A running oneshot is "activating", which is-active's exit code treats as not active.
if systemctl is-active reel-bot-daily-tick.service reel-bot-daily-start.service | grep -q '^activ'; then
  echo "a daily run is active; updating on the next try"
  exit 0
fi
git merge -q --ff-only origin/master
uv sync --locked
install -m 644 deploy/systemd/reel-bot-* /etc/systemd/system/
systemctl daemon-reload
echo "deployed $(git rev-parse --short HEAD)"
