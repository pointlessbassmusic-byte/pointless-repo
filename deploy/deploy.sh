#!/usr/bin/env bash
# Deploy latest main to the Linode server and restart both engines. Run from the laptop:
#   ./deploy/deploy.sh
set -euo pipefail

SERVER="${SERVER:-root@97.107.138.196}"
DEST="/opt/pointless-repo"

echo "==> Deploying to $SERVER"
ssh "$SERVER" bash -s <<EOF
set -euo pipefail
cd "$DEST"
git fetch origin main
git reset --hard origin/main

for eng in polymarket-bot kalshi-engine; do
  cd "$DEST/\$eng"
  ./.venv/bin/pip install -q -r requirements.txt
done

cp "$DEST"/deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl restart polymarket-bot kalshi-engine
systemctl --no-pager status polymarket-bot kalshi-engine | head -20
EOF
echo "==> Deploy complete"
