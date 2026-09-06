#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${HOME}/sports-bot-v2"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

printf '\n== Polymarket sports bot v2 setup ==\n'
printf 'Install directory: %s\n\n' "$APP_DIR"

if command -v sudo >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip tmux ca-certificates
else
  apt-get update
  apt-get install -y python3 python3-venv python3-pip tmux ca-certificates
fi

mkdir -p "$APP_DIR"
cp "$SOURCE_DIR/trading_bot.py" "$APP_DIR/"
cp "$SOURCE_DIR/history_downloader.py" "$APP_DIR/"
cp "$SOURCE_DIR/analyze_history.py" "$APP_DIR/"
cp "$SOURCE_DIR/requirements.txt" "$APP_DIR/"
cd "$APP_DIR"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel
.venv/bin/python -m pip install -r requirements.txt

if [[ ! -f .env ]]; then
  cat > .env <<'ENV'
# ---- mode -------------------------------------------------------------
DRY_RUN=true
# Live trading needs ALL THREE of the following, uncommented, plus funds:
# DRY_RUN=false
# LIVE_CONFIRM=I-ACCEPT-FULL-LOSS-RISK
# PRIVATE_KEY=0x...            # exported Polymarket wallet key — keep secret
# POLY_FUNDER=0x...            # proxy wallet address (if using email/Magic login)
# POLY_SIGNATURE_TYPE=1        # 1 = email/Magic proxy, 2 = browser-wallet proxy

# ---- allocation per sport (USD of paper/live bankroll; omit to disable) ----
SPORT_ALLOCATIONS=tennis=150,table_tennis=100,mlb=50

# ---- fees (Polymarket sports taker rate; fee = shares*rate*p*(1-p)) ----
TAKER_FEE_RATE=0.05

# ---- risk -------------------------------------------------------------
DAILY_MAX_DRAWDOWN_PCT=10
SESSION_PROFIT_LOCK_PCT=0        # e.g. 20 = halt new entries after +20%
KILL_FILE=STOP                   # `touch STOP` halts entries instantly

# ---- plumbing ---------------------------------------------------------
STRATEGY_PARAMS_FILE=strategy_params.json
DISCOVERY_INTERVAL_SECONDS=600
DASHBOARD_INTERVAL_SECONDS=5
STALE_BOOK_SECONDS=20
TRADE_CSV=trades.csv
LOG_FILE=sports_trading_bot.log
CLEAR_DASHBOARD=true
ENV
fi

cat > run_bot.sh <<'RUN'
#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python trading_bot.py "$@"
RUN
chmod +x run_bot.sh trading_bot.py

.venv/bin/python trading_bot.py --self-test

printf '\nSetup complete.\n\n'
printf 'STEP 1 — pull ~1 year of history (hours; safe to re-run/resume):\n'
printf '  cd %s && tmux new -s history ".venv/bin/python history_downloader.py --days 365"\n\n' "$APP_DIR"
printf 'STEP 2 — build the Excel workbook + tuned params:\n'
printf '  .venv/bin/python analyze_history.py\n\n'
printf 'STEP 3 — run the bot (paper by default):\n'
printf '  tmux new -s sportsbot ./run_bot.sh\n'
printf '  detach: Ctrl+B then D · reattach: tmux attach -t sportsbot\n'
printf 'Logs: %s/sports_trading_bot.log · Trades: %s/trades.csv\n' "$APP_DIR" "$APP_DIR"
