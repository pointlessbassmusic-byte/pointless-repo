#!/usr/bin/env bash
# Bring the server's sportsbot up to this branch and switch on the data-only
# recorders. Run ON THE SERVER as root:
#
#   bash /opt/sportsbot/deploy/update_sportsbot.sh [branch]
#
# What it does (nothing here places an order or reads a trading key):
#   1. pulls the branch into /opt/sportsbot and reinstalls the package + extras
#   2. installs every unit under deploy/ (new ones: prematch recorder, the two
#      pilot books and the pilot board) — pilot units are installed DISABLED
#   3. starts/restarts the keyless recorders: prematch (new), LIP, weather,
#      the $100 sim book and its board
#   4. runs `sportsbot doctor` on both pilot configs so you can see exactly
#      what is still missing before any real money (expect secrets FAILs until
#      the keys are in .env)
set -euo pipefail

APP=/opt/sportsbot
BRANCH="${1:-claude/sports-betting-engine-consolidate-m5x0m9}"
BIN="$APP/.venv/bin"

echo "==> code: $BRANCH"
git -C "$APP" fetch origin "$BRANCH"
git -C "$APP" checkout -q "$BRANCH"
git -C "$APP" reset -q --hard "origin/$BRANCH"
"$BIN/pip" install -q --upgrade pip
"$BIN/pip" install -q -e "$APP[kalshi,polymarket_us]"
mkdir -p "$APP/data/ratings" "$APP/logs"
chown -R sportsbot:sportsbot "$APP"

echo "==> systemd units"
for u in "$APP"/deploy/*.service; do
  cp "$u" /etc/systemd/system/
done
systemctl daemon-reload

echo "==> data-only recorders (public endpoints, no keys, never trade)"
systemctl enable --now sportsbot-prematch
systemctl enable --now sportsbot-lip 2>/dev/null || true
systemctl enable --now weather-snapshot 2>/dev/null || true
systemctl restart sportsbot-sim sportsbot-board 2>/dev/null || true
systemctl restart sportsbot-prematch sportsbot-lip 2>/dev/null || true

echo "==> pilot units installed but NOT enabled (real money needs the checklist)"
systemctl disable sportsbot-pilot sportsbot-pilot-pmus sportsbot-pilot-board 2>/dev/null || true

echo "==> preflight on both pilot configs"
for cfg in pilot pilot_pmus; do
  echo "--- $cfg"
  sudo -u sportsbot env SPORTSBOT_LIVE=1 "$BIN/sportsbot" doctor --offline -c "$APP/config/$cfg.yaml" || true
done

echo "==> v3 paper logs (same box, root's home): copy into the repo for analysis"
LOGS=/root/sports-bot-v2
DEST="$APP/polymarket-bot/v3-logs"
if [ -f "$LOGS/edge_log.csv" ] || [ -f "$LOGS/fv_trades.csv" ]; then
  mkdir -p "$DEST"
  for f in edge_log.csv fv_trades.csv; do
    [ -f "$LOGS/$f" ] && cp "$LOGS/$f" "$DEST/$f" && echo "  $f: $(wc -l < "$DEST/$f") lines, $(head -c 400 "$DEST/$f" | head -2 | tail -1 | cut -c1-60)..."
  done
  chown -R sportsbot:sportsbot "$DEST"
  # Push them if this clone can push (a token in the remote URL or a
  # credential helper); otherwise say so, and they can be uploaded in chat.
  git -C "$APP" add polymarket-bot/v3-logs
  if git -C "$APP" -c user.name=server -c user.email=server@sportsbot \
       commit -q -m "v3 paper logs from the server ($(date -u +%F))" 2>/dev/null; then
    if git -C "$APP" push -q origin "$BRANCH" 2>/dev/null; then
      echo "  pushed to origin/$BRANCH"
    else
      git -C "$APP" reset -q --soft HEAD~1   # undo ONLY the commit just made
      echo "  NOT pushed (no git credentials on the server). Files are in $DEST — upload them in chat."
    fi
  else
    echo "  logs unchanged since last run; nothing to commit"
  fi
else
  echo "  no logs at $LOGS (edge_log.csv / fv_trades.csv); skipped"
fi

echo
systemctl --no-pager --type=service --state=running | grep -E "sportsbot|weather" || true
echo
echo "Recorder log:   journalctl -u sportsbot-prematch -f"
echo "Forward test:   sudo -u sportsbot $BIN/python -m sportsbot.signals.prematch --db $APP/data/prematch.sqlite --report"
echo "Board (sim):    $APP/data/board.html"
echo
echo "To go live later (per docs/LIVE_PILOT_PLAN_2026-10-08.md): keys in $APP/.env,"
echo "accounts.real.starting_balance = the deposit, doctor exits 0, verify-fees, then"
echo "  systemctl enable --now sportsbot-pilot        # Kalshi (KALSHI_ENV=demo first)"
echo "  systemctl enable --now sportsbot-pilot-pmus   # Polymarket US (after the 1-contract checks)"
echo "  systemctl enable --now sportsbot-pilot-board"
