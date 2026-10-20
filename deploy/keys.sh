#!/bin/bash
# One prompt for every key the $1 maker test needs (docs/MAKER_TEST.md).
# Run as root on the server: bash /opt/sportsbot/deploy/keys.sh
# Re-run any time; pressing Enter skips a question. Writes /opt/sportsbot/.env
# (mode 600, backed up first) and secrets/kalshi.pem; never prints a secret.
umask 077
B=/opt/sportsbot; ENV=$B/.env; PEM=$B/secrets/kalshi.pem
mkdir -p $B/secrets; touch "$ENV"; cp -p "$ENV" "$ENV.bak.$(date +%s)"
setkey() { grep -v "^$1=" "$ENV" > "$ENV.tmp"; printf '%s=%s\n' "$1" "$2" >> "$ENV.tmp"; mv "$ENV.tmp" "$ENV"; }

echo "=== KALSHI: kalshi.com/account/profile > API Keys > Create New API Key > choose RSA ==="
read -rp "Kalshi Key ID (Enter to skip): " KID
if [ -n "$KID" ]; then
  echo "Paste the Kalshi PRIVATE key (all lines, BEGIN to END), then press Enter:"
  : > "$PEM.new"
  while IFS= read -r line; do printf '%s\n' "$line" >> "$PEM.new"; [[ $line == *"-----END"* ]] && break; done
  if ! openssl pkey -in "$PEM.new" -noout 2>/dev/null; then
    echo "!! Not a valid private key. Kalshi NOT updated."; rm -f "$PEM.new"
  elif openssl pkey -in "$PEM.new" -noout -text 2>/dev/null | head -1 | grep -qi ed25519; then
    echo "!! Ed25519 key. The bot signs with RSA: delete it on Kalshi, create a new key, pick RSA. Kalshi NOT updated."; rm -f "$PEM.new"
  else
    mv "$PEM.new" "$PEM"; setkey KALSHI_API_KEY_ID "$KID"; setkey KALSHI_PRIVATE_KEY_PATH "$PEM"; setkey KALSHI_ENV prod
    echo "Kalshi saved (RSA key OK)."
  fi
fi

echo "=== POLYMARKET US: polymarket.us/developer ==="
read -rp "Polymarket US Key ID (Enter to skip): " PID
if [ -n "$PID" ]; then
  read -rsp "Polymarket US Secret Key (hidden): " PSEC; echo
  setkey POLYMARKET_US_KEY_ID "$PID"; setkey POLYMARKET_US_SECRET_KEY "$PSEC"; echo "Polymarket US saved."; PM=1
fi

echo "=== THE ODDS API (optional; NHL/NFL start times on Kalshi): the-odds-api.com ==="
read -rp "Odds API key (Enter to skip): " ODDS
if [ -n "$ODDS" ]; then setkey ODDS_API_KEY "$ODDS"; echo "Odds API saved."; fi

echo "=== TEST CONFIG ==="
read -rp "Dollars actually deposited on Kalshi [100]: " DEP; DEP=${DEP:-100}
C=$B/config/maker_test.yaml
sed -i 's/^mode: paper /mode: live  /' "$C"
sed -i "/^  real:/{n;s/starting_balance: [0-9.]*/starting_balance: $DEP/}" "$C"
if awk -v d="$DEP" 'BEGIN{exit !(d<100)}'; then sed -i "s/^  amount: [0-9.]* /  amount: $DEP /" "$C"; fi
if [ ! -f $B/config/allocation.local.yaml ] && [ -f $B/config/allocation_maker.yaml ]; then
  cp $B/config/allocation_maker.yaml $B/config/allocation.local.yaml
fi

chown -R sportsbot:sportsbot "$ENV" $B/secrets; chown sportsbot:sportsbot $B/config/allocation.local.yaml 2>/dev/null
chmod 600 "$ENV"
if [ -n "$PM" ]; then systemctl restart sportsbot-ustape 2>/dev/null; fi
echo; echo "=== CHECK (only config.live_gate should WARN; golive.sh clears it) ==="
cd $B && sudo -u sportsbot .venv/bin/sportsbot doctor -c config/maker_test.yaml
