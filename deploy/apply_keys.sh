#!/bin/bash
# Server half of the one-paste Windows setup (docs/MAKER_TEST.md). PowerShell
# uploads /root/env.upload (KEY=VALUE lines) and, optionally,
# /root/kalshi.pem.upload, then runs: bash apply_keys.sh <deposit_usd>.
# Updates the code, installs the keys (Kalshi key must be RSA), sets the maker
# test to live with the deposit, gives the touch arms the learning budget in
# config/allocation.local.yaml, deletes the uploads and runs the preflight.
# Real orders still need deploy/golive.sh and a typed LIVE.
set -e
umask 077
B=/opt/sportsbot; ENV=$B/.env; PEM=$B/secrets/kalshi.pem; DEP=${1:-100}
cd $B
. $B/deploy/maker_local.sh
[ -f "$MAKER_LOCAL" ] || maker_local_config "$DEP"            # carries old hand edits out of the tracked file
bash $B/deploy/update.sh
mkdir -p $B/secrets; touch "$ENV"; cp -p "$ENV" "$ENV.bak.$(date +%s)"
setkey() { grep -v "^$1=" "$ENV" > "$ENV.tmp" || true; printf '%s=%s\n' "$1" "$2" >> "$ENV.tmp"; mv "$ENV.tmp" "$ENV"; }

KOK=""
if [ -s /root/kalshi.pem.upload ]; then
  sed -i 's/\r$//' /root/kalshi.pem.upload
  if ! openssl pkey -in /root/kalshi.pem.upload -noout 2>/dev/null; then
    echo "!! Kalshi key file is not a valid private key -- Kalshi NOT updated"
  elif openssl pkey -in /root/kalshi.pem.upload -noout -text 2>/dev/null | head -1 | grep -qi ed25519; then
    echo "!! Kalshi key is Ed25519 -- the bot needs RSA (create a new Kalshi key, pick RSA). Kalshi NOT updated"
  else
    mv /root/kalshi.pem.upload "$PEM"; KOK=1; echo "Kalshi RSA key installed"
  fi
fi
PM=""
if [ -s /root/env.upload ]; then
  sed -i 's/\r$//' /root/env.upload
  while IFS='=' read -r k v; do
    [ -z "$k" ] || [ -z "$v" ] && continue
    case "$k" in
      KALSHI_API_KEY_ID) [ -n "$KOK" ] || [ -s "$PEM" ] || { echo "!! no valid Kalshi key file, skipping KALSHI_API_KEY_ID"; continue; }
                         setkey KALSHI_API_KEY_ID "$v"; setkey KALSHI_PRIVATE_KEY_PATH "$PEM"; setkey KALSHI_ENV prod ;;
      POLYMARKET_US_KEY_ID|POLYMARKET_US_SECRET_KEY) setkey "$k" "$v"; PM=1 ;;
      ODDS_API_KEY) setkey "$k" "$v" ;;
      *) echo "ignoring unknown key $k" ;;
    esac
  done < /root/env.upload
fi
shred -u /root/env.upload /root/kalshi.pem.upload 2>/dev/null || rm -f /root/env.upload /root/kalshi.pem.upload

maker_local_config "$DEP"
C=$MAKER_LOCAL; A=$B/config/allocation.local.yaml
chown -R sportsbot:sportsbot "$ENV" $B/secrets "$A"; chmod 600 "$ENV" "$PEM" 2>/dev/null || true
if [ -n "$PM" ]; then
  [ -f /etc/systemd/system/sportsbot-ustape.service ] || cp $B/deploy/sportsbot-ustape.service /etc/systemd/system/
  systemctl daemon-reload; systemctl enable --now sportsbot-ustape >/dev/null 2>&1; systemctl restart sportsbot-ustape || true
fi
echo; echo "keys in .env:"; grep -oE '^(KALSHI_[A-Z_]+|POLYMARKET_US_[A-Z_]+|ODDS_API_KEY)=' "$ENV" || true
echo; grep -nE "^mode|starting_balance" "$C"; grep -nE "budget_usd|touch/maker" "$A" | head -4
echo; echo "=== PREFLIGHT (ready when nothing says FAIL; config.live_gate WARN is expected) ==="
sudo -u sportsbot $B/.venv/bin/sportsbot doctor -c config/maker_test.local.yaml
