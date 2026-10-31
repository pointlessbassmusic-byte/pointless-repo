#!/usr/bin/env bash
# Daily unattended update of the PAPER stack on the server (sportsbot-update.timer).
# Pulls main fast-forward only, refreshes the venv, refits MLB (installs the
# committed seed where the Stats API refuses this host), and restarts the paper
# units. It NEVER touches the live unit `sportsbot`: code reaching real money is
# the operator's deliberate restart after reading the change.
#
# "Deploy" means: pip install + restart, then record the commit in
# data/deployed_sha. It runs whenever HEAD differs from that record, so a step
# that fails after a successful pull is retried the next day (comparing HEAD
# before/after the pull lost it: the next run saw no new commits and left the
# services on old code for good). Every run writes data/update_status.json
# (ok / failed + the step); `sportsbot doctor` reports it, because a failing
# oneshot under a healthy-looking timer is otherwise silent.
set -euo pipefail
APP_DIR="${SPORTSBOT_APP_DIR:-/opt/sportsbot}"
STATUS="$APP_DIR/data/update_status.json"
DEPLOYED="$APP_DIR/data/deployed_sha"
step=start
report() {
  mkdir -p "$APP_DIR/data"
  printf '{"status": "%s", "step": "%s", "at": "%s", "head": "%s", "deployed": "%s"}\n' \
    "$1" "$2" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(git -C "$APP_DIR" rev-parse --verify -q HEAD || echo unknown)" \
    "$(cat "$DEPLOYED" 2>/dev/null || echo none)" > "$STATUS"
}
trap 'report failed "$step"; echo "update: FAILED at step $step" >&2' ERR
cd "$APP_DIR"
mkdir -p "$APP_DIR/data"
# Anything root touched (a hand-run `git pull`, an edited .env) must belong
# to the service user again, or its pull fails with "insufficient permission
# for adding an object to repository database" (seen 2026-10-13).
step=chown
chown -R sportsbot:sportsbot "$APP_DIR"
step=pull
sudo -u sportsbot git pull --ff-only --quiet origin main
head=$(git rev-parse HEAD)
deployed=$(cat "$DEPLOYED" 2>/dev/null || echo none)
if [ "$head" != "$deployed" ]; then
  echo "update: $deployed -> $head"
  step=pip
  "$APP_DIR/.venv/bin/pip" install -q -e "$APP_DIR[all]"
fi
step=fit
sudo -u sportsbot "$APP_DIR/.venv/bin/sportsbot" fit baseball || echo "update: fit baseball failed (see above); ratings unchanged"
# Data-only collectors that later commits add (public reads, no orders, no
# keys) are installed once from deploy/ and then restarted with the paper
# units. Only names on this list; never the live unit.
step=units
for unit in sportsbot-freshwatch sportsbot-ustape sportsbot-booklines; do
  if [ -f "$APP_DIR/deploy/$unit.service" ] && [ ! -f "/etc/systemd/system/$unit.service" ]; then
    cp "$APP_DIR/deploy/$unit.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now "$unit"
    echo "update: installed and started $unit"
  fi
done
if [ "$head" != "$deployed" ]; then
  # the runner cancels its resting orders (with tape shadows) on SIGTERM and
  # sweeps anything orphaned on the next start, so a restart loses no evidence
  step=restart
  systemctl restart sportsbot-sim sportsbot-board sportsbot-boardserve weather-snapshot
  systemctl restart sportsbot-freshwatch 2>/dev/null || true
  systemctl restart sportsbot-ustape 2>/dev/null || true
  systemctl restart sportsbot-booklines 2>/dev/null || true
  echo "$head" > "$DEPLOYED"
  echo "update: paper units restarted"
else
  echo "update: no new commits; units left running"
fi
report ok done
