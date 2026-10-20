#!/bin/bash
# Turn the $1 maker test on: points sportsbot.service at config/maker_test.yaml with
# SPORTSBOT_LIVE=1 (the second of the two live gates), after a passing preflight
# and a typed confirmation. Stop with: touch /opt/sportsbot/data/KILL_SWITCH
# or: bash /opt/sportsbot/deploy/golive.sh stop
set -e
B=/opt/sportsbot; D=/etc/systemd/system/sportsbot.service.d
if [ "$1" = "stop" ]; then
  systemctl stop sportsbot; rm -f $D/maker_test.conf; systemctl daemon-reload
  echo "Live test stopped; sportsbot.service back to its default (paper) command."; exit 0
fi
cd $B
grep -q '^mode: live' config/maker_test.yaml || { echo "config/maker_test.yaml is not mode: live -- run keys.sh first"; exit 1; }
if sudo -u sportsbot SPORTSBOT_LIVE=1 .venv/bin/sportsbot doctor -c config/maker_test.yaml | grep -q "overall: FAIL"; then
  echo "Preflight FAILS -- not going live. Run: bash $B/deploy/keys.sh"; exit 1
fi
read -rp "Type LIVE to place real \$1 orders on Kalshi: " OK
[ "$OK" = "LIVE" ] || { echo "Not confirmed; nothing changed."; exit 1; }
mkdir -p $D
cat > $D/maker_test.conf <<EOF
[Service]
Environment=SPORTSBOT_LIVE=1
ExecStart=
ExecStart=$B/.venv/bin/sportsbot run -c $B/config/maker_test.yaml
EOF
systemctl daemon-reload && systemctl restart sportsbot
sleep 5; systemctl --no-pager status sportsbot | head -5
echo "Live. Follow it: journalctl -u sportsbot -f   Stop: bash $B/deploy/golive.sh stop"
