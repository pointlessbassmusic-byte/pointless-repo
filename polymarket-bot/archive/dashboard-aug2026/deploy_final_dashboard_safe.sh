#!/usr/bin/env bash
set +e
set +u
set +o pipefail 2>/dev/null || true

echo "=================================================="
echo "FINAL DASHBOARD-SAFE PAPER ENGINE DEPLOYMENT"
echo "=================================================="

read -rsp "Paste NEW rotated SharpOracle API key: " SHARP_KEY
echo

if [ -z "$SHARP_KEY" ]; then
    echo "No API key supplied. Nothing changed."
    exit 0
fi

install -m 600 /dev/null /root/.paper_trader.env
printf 'SHARP_ORACLE_API_KEY=%s\n' "$SHARP_KEY" >/root/.paper_trader.env
unset SHARP_KEY

bash <<'INNER'
set -Eeuo pipefail

SOURCE_PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NEW_SOURCE="$SOURCE_PACKAGE_DIR/paper_trader_final.py"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="/root/paper-trader-backups/$STAMP"
SERVICE="paper-trader"

mkdir -p "$BACKUP"

for f in \
    /root/paper_trader.py \
    /root/paper_output.log \
    /root/sim_metrics.csv \
    /root/paper_trades.csv \
    /root/paper_metrics_history.csv
do
    [ -f "$f" ] && cp -a "$f" "$BACKUP/" || true
done

ss -ltnp >"$BACKUP/listeners_before.txt" 2>/dev/null || true
ps auxww >"$BACKUP/processes_before.txt"

if [ ! -f /root/sharp_oracle.py ]; then
    echo "ERROR: /root/sharp_oracle.py is missing."
    exit 1
fi

python3 -m py_compile "$NEW_SOURCE"

systemctl stop "$SERVICE" 2>/dev/null || true

for pid in $(pgrep -f '^python3 paper_trader\.py$|^python3 /root/paper_trader\.py$|^/usr/bin/python3 /root/paper_trader\.py$' 2>/dev/null || true); do
    kill "$pid" 2>/dev/null || true
done

systemctl disable --now tennis-h2h-final 2>/dev/null || true
systemctl disable --now tennis-h2h-v10 2>/dev/null || true
systemctl disable --now tennis-h2h-v11 2>/dev/null || true
pkill -f '[p]aper_quoter.py' 2>/dev/null || true

sleep 2

cp "$NEW_SOURCE" /root/paper_trader.py
chmod 700 /root/paper_trader.py

cat >/etc/systemd/system/paper-trader.service <<'UNIT'
[Unit]
Description=Sharp-Anchor Tennis H2H Paper Research Engine
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=root
WorkingDirectory=/root
EnvironmentFile=/root/.paper_trader.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 -u /root/paper_trader.py
Restart=always
RestartSec=5
TimeoutStopSec=20
KillSignal=SIGINT
StandardOutput=append:/root/paper_output.log
StandardError=append:/root/paper_output.log

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl reset-failed "$SERVICE" 2>/dev/null || true
systemctl enable --now "$SERVICE"

sleep 12

if ! systemctl is-active --quiet "$SERVICE"; then
    echo "ERROR: paper-trader did not stay active."
    systemctl status "$SERVICE" --no-pager -l || true
    tail -n 100 /root/paper_output.log || true
    exit 1
fi

ss -ltnp >"$BACKUP/listeners_after.txt" 2>/dev/null || true

echo
echo "=================================================="
echo "DEPLOYMENT STATUS"
echo "=================================================="
systemctl is-active "$SERVICE"
systemctl is-enabled "$SERVICE"
systemctl show "$SERVICE" -p MainPID -p NRestarts -p ActiveState -p SubState

echo
echo "Dashboard metrics contract:"
head -n 2 /root/sim_metrics.csv || true

echo
echo "Listener comparison (dashboard should remain intact):"
diff -u "$BACKUP/listeners_before.txt" "$BACKUP/listeners_after.txt" || true

echo
echo "Recent output:"
tail -n 60 /root/paper_output.log || true

echo
echo "Backup:"
echo "$BACKUP"
INNER

STATUS=$?
echo
echo "Deployment child returned status: $STATUS"
echo "This terminal remains open."
