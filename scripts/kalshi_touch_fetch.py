"""Fetch settled Kalshi game markets + pre-game trade tape for
docs/KALSHI_TOUCH_PREREG_2026-10-14.md. Public endpoints only."""
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

sys.path.insert(0, "/home/user/pointless-repo")
from sportsbot.exchanges.kalshi import mlb_first_pitch  # noqa: E402

B = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = {"KXMLBGAME": "mlb", "KXNHLGAME": "nhl", "KXNFLGAME": "nfl", "KXNCAAFGAME": "ncaaf",
          "KXEPLGAME": "soccer", "KXUCLGAME": "soccer", "KXLALIGAGAME": "soccer", "KXMLSGAME": "soccer"}
DB = sys.argv[1]
c = httpx.Client(timeout=30)


def get(path, params):
    for a in range(6):
        try:
            r = c.get(B + path, params=params)
            if r.status_code == 429:
                time.sleep(1.5 * (a + 1))
                continue
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError:
            time.sleep(1.5 * (a + 1))
    return {}


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


conn = sqlite3.connect(DB)
conn.executescript("""CREATE TABLE IF NOT EXISTS m (ticker TEXT PRIMARY KEY, event TEXT, sport TEXT,
  cut REAL, result INTEGER, truncated INTEGER, n INTEGER);
CREATE TABLE IF NOT EXISTS t (ticker TEXT, ts REAL, yes REAL, taker TEXT, size REAL);""")
now = datetime.now(timezone.utc)
since = int((now - timedelta(days=30)).timestamp())
done = {r[0] for r in conn.execute("SELECT ticker FROM m")}
for series, sport in SERIES.items():
    cur, markets = None, []
    while True:
        p = {"series_ticker": series, "status": "settled", "limit": 1000, "min_close_ts": since}
        if cur:
            p["cursor"] = cur
        d = get("/markets", p)
        markets += d.get("markets", [])
        cur = d.get("cursor")
        if not cur or not d.get("markets"):
            break
    markets = [m for m in markets if m.get("result") in ("yes", "no")
               and float(m.get("volume_fp") or 0) > 0 and m["ticker"] not in done]
    print(series, len(markets), "markets", flush=True)
    for m in markets:
        tk = m["ticker"]
        if series == "KXMLBGAME":
            fp = mlb_first_pitch(tk)
            if fp is None:
                continue
            cut = fp
        else:
            if not m.get("occurrence_datetime"):
                continue
            cut = ts(m["occurrence_datetime"]) - timedelta(hours=4)
        cut_ts = int(cut.timestamp())
        rows, cursor, pages = [], None, 0
        while pages < 5:
            p = {"ticker": tk, "limit": 1000, "min_ts": cut_ts - 21600, "max_ts": cut_ts}
            if cursor:
                p["cursor"] = cursor
            d = get("/markets/trades", p)
            tr = d.get("trades", [])
            for x in tr:
                rows.append((tk, ts(x["created_time"]).timestamp(), float(x["yes_price_dollars"]),
                             x.get("taker_side"), float(x.get("count_fp") or 0)))
            pages += 1
            cursor = d.get("cursor")
            if not cursor or not tr:
                break
        truncated = int(bool(cursor) and pages >= 5)
        conn.executemany("INSERT INTO t VALUES (?,?,?,?,?)", rows)
        conn.execute("INSERT OR REPLACE INTO m VALUES (?,?,?,?,?,?,?)",
                     (tk, m["event_ticker"], sport, cut.timestamp(), 1 if m["result"] == "yes" else 0,
                      truncated, len(rows)))
        conn.commit()
print("done", flush=True)
