#!/usr/bin/env bash
# Export the server's live results as small files the telemetry workflow
# pulls into the repo. Read-only: nothing here trades or changes config.
#   bash /opt/sportsbot/deploy/export_reports.sh
set -uo pipefail
APP=/opt/sportsbot
BIN="$APP/.venv/bin"
OUT="$APP/data/export"
mkdir -p "$OUT"
run() { sudo -u sportsbot env "$@"; }

# Boards (static HTML) and the ops JSON for the sim and both pilot books.
run "$BIN/sportsbot" board -c "$APP/config/sim.yaml" --out "$OUT/board_sim.html" --refresh 0 >/dev/null 2>&1 || true
for cfg in pilot pilot_pmus; do
  [ -f "$APP/data/$cfg.sqlite" ] || continue
  run SPORTSBOT_LIVE=1 "$BIN/sportsbot" board -c "$APP/config/$cfg.yaml" --out "$OUT/board_$cfg.html" --refresh 0 >/dev/null 2>&1 || true
  run SPORTSBOT_LIVE=1 "$BIN/sportsbot" status -c "$APP/config/$cfg.yaml" > "$OUT/status_$cfg.txt" 2>&1 || true
  run SPORTSBOT_LIVE=1 "$BIN/sportsbot" allocation -c "$APP/config/$cfg.yaml" > "$OUT/allocation_$cfg.txt" 2>&1 || true
done
# The live $1 maker fill-share test (docs/MAKER_TEST.md): golive.sh runs the
# git-ignored operator copy on its own database.
MT="$APP/config/maker_test.local.yaml"
if [ -f "$MT" ] && [ -f "$APP/data/maker_test.sqlite" ]; then
  run SPORTSBOT_LIVE=1 "$BIN/sportsbot" status -c "$MT" > "$OUT/status_maker_test.txt" 2>&1 || true
  run SPORTSBOT_LIVE=1 "$BIN/sportsbot" allocation -c "$MT" > "$OUT/allocation_maker_test.txt" 2>&1 || true
  run SPORTSBOT_LIVE=1 "$BIN/sportsbot" maker-report -c "$MT" --mode live > "$OUT/maker_report.txt" 2>&1 || true
fi
run "$BIN/sportsbot" status -c "$APP/config/sim.yaml" > "$OUT/status_sim.txt" 2>&1 || true
run "$BIN/sportsbot" allocation -c "$APP/config/sim.yaml" > "$OUT/allocation_sim.txt" 2>&1 || true

# Forward test (pre-match recorder) and recorder health.
[ -f "$APP/data/prematch.sqlite" ] && run "$BIN/python" -m sportsbot.signals.prematch --db "$APP/data/prematch.sqlite" --report > "$OUT/prematch_forward_test.json" 2>/dev/null || true

# The other forward records and the Polymarket US tape (all read-only reads
# of local databases; us-tape --verify also reads the public /bbo).
[ -f "$APP/data/book_lines.sqlite" ] && run "$BIN/sportsbot" book-lines --report --db "$APP/data/book_lines.sqlite" > "$OUT/book_lines_report.txt" 2>&1 || true
[ -f "$APP/data/fresh_watch.sqlite" ] && run "$BIN/sportsbot" fresh-watch --report --db "$APP/data/fresh_watch.sqlite" > "$OUT/fresh_watch_report.txt" 2>&1 || true
run "$BIN/sportsbot" us-tape --status --verify --db "$APP/data/us_tape.sqlite" > "$OUT/us_tape.txt" 2>&1 || true

# The queue recorder behind docs/QUEUE_FILL_MODEL_PREREG_2026-10-20.md: health
# only (rows and span). Its verdict is read once at day 30, never nightly.
[ -f "$APP/data/queue_forward.sqlite" ] && run "$BIN/python" - "$APP/data/queue_forward.sqlite" > "$OUT/queue_forward_health.txt" 2>&1 <<'PY' || true
import sqlite3, sys
from datetime import datetime, timezone
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
fmt = lambda t: "-" if t is None else datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%MZ")
print("games with a Kalshi ticker:", c.execute("SELECT COUNT(*) FROM games WHERE k_ticker IS NOT NULL").fetchone()[0])
for v in ("kalshi", "us", "pm"):
    n, lo, hi = c.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM samples WHERE venue=?", (v,)).fetchone()
    print(f"samples {v:6s} {n:9d}  {fmt(lo)} .. {fmt(hi)}")
n, lo, hi = c.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM prints WHERE venue='kalshi'").fetchone()
print(f"prints  kalshi {n:9d}  {fmt(lo)} .. {fmt(hi)}")
PY

# Service health and the last lines of each recorder's log.
systemctl --no-pager --type=service --state=running | grep -E "sportsbot|weather" > "$OUT/services.txt" 2>&1 || true
for u in sportsbot sportsbot-prematch sportsbot-sim sportsbot-pilot sportsbot-pilot-pmus sportsbot-lip sportsbot-booklines sportsbot-freshwatch sportsbot-ustape sportsbot-queuelog sportsbot-reddit; do
  journalctl -u "$u" -n 40 --no-pager > "$OUT/log_$u.txt" 2>/dev/null || true
done

# v3 paper logs, if the fair-value bot still runs on this box.
for f in edge_log.csv fv_trades.csv; do
  [ -f "/root/sports-bot-v2/$f" ] && tail -n 20000 "/root/sports-bot-v2/$f" > "$OUT/v3_$f"
done

date -u +%FT%TZ > "$OUT/exported_at.txt"
chown -R sportsbot:sportsbot "$OUT"
echo "exported to $OUT"
