#!/usr/bin/env bash
# One-time bootstrap of the Linode server. Run from the laptop, in the repo root:
#   ./deploy/setup_server.sh
set -euo pipefail

SERVER="${SERVER:-root@97.107.138.196}"
REPO_URL="https://github.com/pointlessbassmusic-byte/pointless-repo.git"
DEST="/opt/pointless-repo"

echo "==> Bootstrapping $SERVER"
ssh "$SERVER" bash -s <<EOF
set -euo pipefail
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip

if [ ! -d "$DEST/.git" ]; then
  git clone "$REPO_URL" "$DEST"
fi
cd "$DEST" && git pull origin main

for eng in polymarket-bot kalshi-engine; do
  cd "$DEST/\$eng"
  [ -d .venv ] || python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
  [ -f .env ] || cp .env.example .env
  mkdir -p data
done

cp "$DEST"/deploy/systemd/*.service "$DEST"/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable polymarket-bot kalshi-engine
systemctl enable --now kalshi-snapshots.timer
echo "Setup done. Fill in $DEST/polymarket-bot/.env and $DEST/kalshi-engine/.env,"
echo "then: systemctl start polymarket-bot kalshi-engine"
echo "Kalshi snapshot timer is live: systemctl list-timers kalshi-snapshots.timer"
EOF
