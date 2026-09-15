#!/usr/bin/env bash
# One-time setup for an Ubuntu server (tested profile: Linode 2 CPU / 4GB).
# Run as root:  bash deploy/setup_server.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/pointlessbassmusic-byte/pointless-repo.git}"
BRANCH="${BRANCH:-main}"
APP_DIR=/opt/sportsbot

echo "==> system packages"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git ufw fail2ban unattended-upgrades chrony

echo "==> firewall: ssh only inbound"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

echo "==> time sync (order timestamps + API signatures need a true clock)"
systemctl enable --now chrony

echo "==> service user"
id -u sportsbot &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin sportsbot

echo "==> code"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" fetch origin "$BRANCH" && git -C "$APP_DIR" checkout "$BRANCH" && git -C "$APP_DIR" pull origin "$BRANCH"
fi

echo "==> python env"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -e "$APP_DIR[all]"

mkdir -p "$APP_DIR/data/ratings" "$APP_DIR/logs"

if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo "!!  Edit $APP_DIR/.env with your keys. SPORTSBOT_LIVE stays 0 until paper results earn it."
fi

chown -R sportsbot:sportsbot "$APP_DIR"

echo "==> systemd"
cp "$APP_DIR/deploy/sportsbot.service" /etc/systemd/system/sportsbot.service
systemctl daemon-reload
systemctl enable sportsbot

echo "==> initial model fit (tennis + baseball; table tennis bootstraps from Polymarket)"
sudo -u sportsbot "$APP_DIR/.venv/bin/sportsbot" fit baseball || true
sudo -u sportsbot "$APP_DIR/.venv/bin/sportsbot" fit tennis || true
sudo -u sportsbot "$APP_DIR/.venv/bin/sportsbot" fit table_tennis || true

echo
echo "Setup complete. Start with:   systemctl start sportsbot"
echo "Watch logs with:              journalctl -u sportsbot -f"
echo "Check status with:            sudo -u sportsbot $APP_DIR/.venv/bin/sportsbot status"
