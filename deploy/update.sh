#!/usr/bin/env bash
# Daily unattended update of the PAPER stack on the server (sportsbot-update.timer).
# Pulls main fast-forward only, refreshes the venv, refits MLB (installs the
# committed seed where the Stats API refuses this host), and restarts the paper
# units. It NEVER touches the live unit `sportsbot`: code reaching real money is
# the operator's deliberate restart after reading the change.
set -euo pipefail
APP_DIR=/opt/sportsbot
cd "$APP_DIR"
# Anything root touched (a hand-run `git pull`, an edited .env) must belong
# to the service user again, or its pull fails with "insufficient permission
# for adding an object to repository database" (seen 2026-10-13).
chown -R sportsbot:sportsbot "$APP_DIR"
before=$(git rev-parse HEAD)
sudo -u sportsbot git pull --ff-only --quiet origin main
after=$(git rev-parse HEAD)
if [ "$before" != "$after" ]; then
  echo "update: $before -> $after"
  "$APP_DIR/.venv/bin/pip" install -q -e "$APP_DIR[all]"
fi
sudo -u sportsbot "$APP_DIR/.venv/bin/sportsbot" fit baseball || echo "update: fit baseball failed (see above); ratings unchanged"
if [ "$before" != "$after" ]; then
  # the runner cancels its resting orders (with tape shadows) on SIGTERM and
  # sweeps anything orphaned on the next start, so a restart loses no evidence
  systemctl restart sportsbot-sim sportsbot-board sportsbot-boardserve weather-snapshot
  echo "update: paper units restarted"
else
  echo "update: no new commits; units left running"
fi
