#!/usr/bin/env bash
# Export the server's live results as small files the telemetry workflow
# pulls into the repo. Read-only: nothing here trades or changes config.
#   bash /opt/sportsbot/deploy/export_reports.sh
set -uo pipefail
APP="${SPORTSBOT_APP_DIR:-/opt/sportsbot}"
BIN="$APP/.venv/bin"
OUT="$APP/data/export"
# The configs' paths (logs/, data/...) are relative to the app directory;
# over ssh this script starts in /root, where status/allocation died on
# PermissionError 'logs' (telemetry 2026-11-03) and a relative sqlite path
# would open the wrong book.
cd "$APP" || exit 1
mkdir -p "$OUT"
run() { sudo -u sportsbot env "$@"; }

# Which code the server runs, and whether the daily pull is stuck: a stale
# checkout means none of the merged fixes have reached the box.
{
  # read-only and as the repo's owner: root git refuses another user's repo
  # ("dubious ownership"), and a root fetch leaves objects the daily pull
  # cannot write next to
  echo "server HEAD:  $(run git log -1 --format='%H %cI %s' 2>&1)"
  echo "origin/main:  $(run git ls-remote origin refs/heads/main 2>&1 | cut -f1)"
  echo "local changes (block the ff-only pull if a merged commit touches them):"
  run git status --short --untracked-files=no 2>&1
} > "$OUT/server_git.txt" 2>&1

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
[ -f "$APP/data/prematch.sqlite" ] && run "$BIN/sportsbot" prematch --db "$APP/data/prematch.sqlite" > "$OUT/prematch_forward_test.json" 2>/dev/null || true

# The other forward records and the Polymarket US tape (all read-only reads
# of local databases; us-tape --verify also reads the public /bbo).
[ -f "$APP/data/book_lines.sqlite" ] && run "$BIN/sportsbot" book-lines --report --db "$APP/data/book_lines.sqlite" > "$OUT/book_lines_report.txt" 2>&1 || true
[ -f "$APP/data/fresh_watch.sqlite" ] && run "$BIN/sportsbot" fresh-watch --report --db "$APP/data/fresh_watch.sqlite" > "$OUT/fresh_watch_report.txt" 2>&1 || true
run "$BIN/sportsbot" us-tape --status --verify --db "$APP/data/us_tape.sqlite" > "$OUT/us_tape.txt" 2>&1 || true

# The queue recorder behind docs/QUEUE_FILL_MODEL_PREREG_2026-10-20.md: health
# only. Its verdict is read once at day 30, never nightly. Every query is
# O(1) (rowid ends, no table scans): a COUNT(*) over millions of samples held
# the shared lock past the recorder's busy timeout and crashed it on Nov 4
# and Nov 7. Rows are never deleted, so MAX(rowid) is the row count.
[ -f "$APP/data/queue_forward.sqlite" ] && run "$BIN/python" - "$APP/data/queue_forward.sqlite" > "$OUT/queue_forward_health.txt" 2>&1 <<'PY' || true
import sqlite3, sys
from datetime import datetime, timezone
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=1)
fmt = lambda t: "-" if t is None else datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%MZ")
one = lambda q: (c.execute(q).fetchone() or (None,))[0]
print("games:", one("SELECT MAX(rowid) FROM games"))
for t in ("samples", "prints"):
    print(f"{t:8s} {one(f'SELECT MAX(rowid) FROM {t}') or 0:10d}  "
          f"{fmt(one(f'SELECT ts FROM {t} ORDER BY rowid LIMIT 1'))} .. "
          f"{fmt(one(f'SELECT ts FROM {t} ORDER BY rowid DESC LIMIT 1'))}")
PY

# Service health and the last lines of each recorder's log.
systemctl --no-pager --type=service --state=running | grep -E "sportsbot|weather" > "$OUT/services.txt" 2>&1 || true
[ -f "$APP/data/update_status.json" ] && cp "$APP/data/update_status.json" "$OUT/update_status.json" || true
for u in sportsbot-update sportsbot sportsbot-prematch sportsbot-sim sportsbot-pilot sportsbot-pilot-pmus sportsbot-lip sportsbot-booklines sportsbot-freshwatch sportsbot-ustape sportsbot-queuelog sportsbot-reddit; do
  journalctl -u "$u" -n 40 --no-pager > "$OUT/log_$u.txt" 2>/dev/null || true
done

# v3 paper logs, if the fair-value bot still runs on this box.
for f in edge_log.csv fv_trades.csv; do
  [ -f "/root/sports-bot-v2/$f" ] && tail -n 20000 "/root/sports-bot-v2/$f" > "$OUT/v3_$f"
done

date -u +%FT%TZ > "$OUT/exported_at.txt"
chown -R sportsbot:sportsbot "$OUT"
echo "exported to $OUT"
