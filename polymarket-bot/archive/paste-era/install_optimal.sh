#!/usr/bin/env bash
set -euo pipefail

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="/root/sports-bot-optimal"
VENV_DIR="$BOT_DIR/.venv"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root."
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip tmux ca-certificates

mkdir -p "$BOT_DIR"

if [ -f "$BOT_DIR/optimal_trading_bot.py" ]; then
  BACKUP="$BOT_DIR/optimal_trading_bot.py.backup.$(date -u +%Y%m%dT%H%M%SZ)"
  cp "$BOT_DIR/optimal_trading_bot.py" "$BACKUP"
  echo "Backed up current bot to $BACKUP"
fi

cp "$PACKAGE_DIR/optimal_trading_bot.py" "$BOT_DIR/optimal_trading_bot.py"
chmod 700 "$BOT_DIR/optimal_trading_bot.py"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install "requests>=2.31,<3" "websockets>=14,<17"
# py-clob-client only matters for live mode; install now so the live switch is one env change.
"$VENV_DIR/bin/python" -m pip install "py-clob-client" || echo "WARN: py-clob-client install failed; dry run unaffected."

ENV_FILE="$BOT_DIR/.env"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"
set_env() {
  local key="$1"; local value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

# ---- DRY RUN DEFAULTS (safe) ----
set_env DRY_RUN true
set_env BOOTSTRAP_TRADES false
set_env SPORTS_FEE_RATE 0.05
set_env MAX_RUNTIME_SECONDS 21600          # 6-hour session for real signal sampling
set_env DASHBOARD_INTERVAL_SECONDS 5
set_env STRATEGY_INTERVAL_SECONDS 1
set_env MAX_OPEN_POSITIONS_PER_MODEL 3
set_env MIN_TRADE_NOTIONAL 1.00
set_env MAX_TRADE_NOTIONAL 15.00
set_env ENTRY_MIN_MID 0.55
set_env ENTRY_MAX_MID 0.90
set_env RISK_MULTIPLIER 2.5        # dry-run experiment: 2-3x risk authorized; hard cap 35%/bucket
set_env REVERSAL_TRIGGER_MULT 1.0
set_env CLEAR_DASHBOARD true
set_env LOG_FILE sports_trading_bot_optimal.log
set_env TRADE_CSV dry_run_trades_optimal.csv
# ---- LIVE MODE (leave commented until dry run has PROVEN net-positive) ----
# set_env DRY_RUN false
# set_env LIVE_CONFIRM I_UNDERSTAND_LIVE_RISK
# set_env POLYMARKET_PRIVATE_KEY <your-wallet-private-key>
# set_env POLYMARKET_FUNDER <your-polymarket-funder-address>
# set_env POLYMARKET_SIGNATURE_TYPE 1      # 1 = email/Magic wallet, 0 = EOA/MetaMask

cat > "$BOT_DIR/run_bot.sh" <<'RUN'
#!/usr/bin/env bash
set -euo pipefail
cd /root/sports-bot-optimal
set -a
[ -f .env ] && source .env
set +a
exec /root/sports-bot-optimal/.venv/bin/python /root/sports-bot-optimal/optimal_trading_bot.py
RUN
chmod 700 "$BOT_DIR/run_bot.sh"

cd "$BOT_DIR"
"$VENV_DIR/bin/python" "$BOT_DIR/optimal_trading_bot.py" --self-test

tmux kill-session -t optimalbot 2>/dev/null || true
tmux new-session -d -s optimalbot "cd $BOT_DIR && ./run_bot.sh; status=\$?; echo; echo Bot exited with status \$status; read"

echo
echo "OPTIMAL build installed."
echo "Bot directory: $BOT_DIR"
echo "tmux session:  optimalbot   (attach: tmux attach -t optimalbot)"
echo "Mode:          DRY_RUN=true (simulated fills, live market data)"
echo "Trades CSV:    tail -f $BOT_DIR/dry_run_trades_optimal.csv"
