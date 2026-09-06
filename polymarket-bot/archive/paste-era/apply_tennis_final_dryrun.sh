#!/usr/bin/env bash
set -euo pipefail

BOT_DIR=/root/sports-bot
BOT_FILE="$BOT_DIR/trading_bot.py"
ENV_FILE="$BOT_DIR/.env"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ ! -f "$BOT_FILE" ]]; then
  echo "ERROR: $BOT_FILE not found"
  exit 1
fi

cp "$BOT_FILE" "$BOT_FILE.backup.$STAMP"
mkdir -p "$BOT_DIR/archive"
[[ -f "$BOT_DIR/sports_trading_bot.log" ]] && mv "$BOT_DIR/sports_trading_bot.log" "$BOT_DIR/archive/sports_trading_bot.$STAMP.log"
[[ -f "$BOT_DIR/dry_run_trades.csv" ]] && mv "$BOT_DIR/dry_run_trades.csv" "$BOT_DIR/archive/dry_run_trades.$STAMP.csv"

python3 - <<'PY'
from pathlib import Path
p = Path('/root/sports-bot/trading_bot.py')
s = p.read_text()

anchor = 'RUNTIME = RuntimeConfig()\n'
insert = '''RUNTIME = RuntimeConfig()\n\nENABLED_SPORTS = {\n    name.strip()\n    for name in os.getenv("ENABLED_SPORTS", "Tennis").split(",")\n    if name.strip()\n}\nMIN_ENTRY_PRICE = float(os.getenv("MIN_ENTRY_PRICE", "0.15"))\nMAX_ENTRY_PRICE = float(os.getenv("MAX_ENTRY_PRICE", "0.85"))\n'''
if 'ENABLED_SPORTS = {' not in s:
    if anchor not in s:
        raise SystemExit('Could not find RuntimeConfig anchor')
    s = s.replace(anchor, insert, 1)

old_filter = 'if sport not in SPORT_CONFIGS:\n                    continue'
new_filter = 'if sport not in SPORT_CONFIGS or sport not in ENABLED_SPORTS:\n                    continue'
if old_filter in s:
    s = s.replace(old_filter, new_filter, 1)
elif new_filter not in s:
    raise SystemExit('Could not patch sport filter')

old_price = 'if mid < 0.05 or mid > 0.95:'
new_price = 'if mid < MIN_ENTRY_PRICE or mid > MAX_ENTRY_PRICE:'
if old_price in s:
    s = s.replace(old_price, new_price, 1)
elif new_price not in s:
    raise SystemExit('Could not patch entry price band')

p.write_text(s)
PY

touch "$ENV_FILE"
set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

set_env DRY_RUN true
set_env ENABLED_SPORTS Tennis
set_env INITIAL_BANKROLL_PER_SPORT 150
set_env BASE_ALLOCATION_CAP 5
set_env MAX_OPEN_POSITIONS 1
set_env MAX_LIVE_OPEN_POSITIONS 1
set_env MAX_LIVE_ORDER_USD 5
set_env MAX_LIVE_DAILY_LOSS_USD 6
set_env MIN_SECONDS_BETWEEN_LIVE_ORDERS 300
set_env PAPER_IDLE_PROBE_SECONDS 0
set_env PAPER_IDLE_PROBE_NOTIONAL 0
set_env MIN_ENTRY_PRICE 0.15
set_env MAX_ENTRY_PRICE 0.85
set_env CLEAR_DASHBOARD true

cd "$BOT_DIR"
if [[ -x .venv/bin/python ]]; then
  .venv/bin/python trading_bot.py --self-test
elif [[ -x venv/bin/python ]]; then
  venv/bin/python trading_bot.py --self-test
else
  python3 trading_bot.py --self-test
fi

tmux kill-session -t sportsbot 2>/dev/null || true
tmux new-session -d -s sportsbot "cd $BOT_DIR && ./run_bot.sh; echo BOT_STOPPED; read"

echo "Tennis-only final dry run started in tmux session sportsbot"
echo "Backup: $BOT_FILE.backup.$STAMP"
echo "Archived prior logs/trades under $BOT_DIR/archive"
