"""Kalshi vs Polymarket US: sample both books on the same games every
INTERVAL seconds and store them, for (A) the complement-arb check and
(B) lead-lag. Read-only, public endpoints only."""
import json
import logging
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/home/user/pointless-repo")
from sportsbot.exchanges.kalshi import KalshiClient  # noqa: E402
from sportsbot.exchanges.polymarket_us import PolymarketUSClient  # noqa: E402

logging.basicConfig(level=logging.WARNING)
DB, MINUTES, INTERVAL = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
SPORTS = ["tennis", "baseball", "hockey", "football", "basketball"]


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace(".", " ").replace("-", " ").split())


def pair_key(a, b):
    return tuple(sorted((norm(a), norm(b))))


k, p = KalshiClient(env="prod"), PolymarketUSClient()
pairs = []   # (sport, team, kalshi_market, pmus_market, team_is_pm_home)
for sport in SPORTS:
    try:
        km, pm = k.list_sports_markets(sport), p.list_sports_markets(sport)
    except Exception as exc:
        print("discovery failed", sport, exc)
        continue
    by_pair = {}
    for m in pm:
        by_pair.setdefault(pair_key(m.home, m.away), []).append(m)
    seen = set()
    for m in km:   # Kalshi: YES = m.home's team
        cands = by_pair.get(pair_key(m.home, m.away), [])
        if not cands:
            continue
        # several games between the same teams (a series): need a date match
        kdate = m.market_id.split("-")[1][:7]          # e.g. 26OCT15
        best = None
        for c in cands:
            if c.start_time is None:
                continue
            for d in (c.start_time, c.start_time - timedelta(hours=10)):
                if d.strftime("%y%b%d").upper() == kdate:
                    best = c
        if best is None or best.market_id in seen:
            continue                                  # one Kalshi side per game
        seen.add(best.market_id)
        pairs.append((sport, norm(m.home), m, best, norm(best.home) == norm(m.home)))
print(f"{len(pairs)} matched games:", {s: sum(1 for x in pairs if x[0] == s) for s in SPORTS}, flush=True)

conn = sqlite3.connect(DB)
conn.execute("CREATE TABLE IF NOT EXISTS q (ts REAL, sport TEXT, game TEXT, start REAL,"
             " kbid REAL, kask REAL, kbsz REAL, kasz REAL, pbid REAL, pask REAL, pbsz REAL, pasz REAL)")
conn.execute("CREATE TABLE IF NOT EXISTS meta (game TEXT PRIMARY KEY, j TEXT)")
for sport, team, km, pm, same in pairs:
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (km.market_id, json.dumps(
        {"sport": sport, "team": team, "pmus": pm.market_id, "pm_home_is_team": same})))
conn.commit()

end = time.time() + MINUTES * 60
while time.time() < end:
    t0 = time.time()
    for sport, team, km, pm, same in pairs:
        try:
            kq, pq = k.get_quote(km), p.get_quote(pm)
        except Exception:
            continue
        # PM US quotes are P(pm.home wins); flip to P(team wins) when needed
        pb, pa = pq.bid, pq.ask
        pbs = pq.bids[0].size if pq.bids else None
        pas = pq.asks[0].size if pq.asks else None
        if not same:
            pb, pa = (None if pq.ask is None else 1 - pq.ask), (None if pq.bid is None else 1 - pq.bid)
            pbs, pas = pas, pbs
        st = pm.start_time.timestamp() if pm.start_time else None
        conn.execute("INSERT INTO q VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (time.time(), sport, km.market_id, st, kq.bid, kq.ask,
                      kq.bids[0].size if kq.bids else None, kq.asks[0].size if kq.asks else None,
                      pb, pa, pbs, pas))
    conn.commit()
    print(datetime.now(timezone.utc).strftime("%H:%M:%S"), f"cycle {time.time() - t0:.1f}s", flush=True)
    time.sleep(max(1.0, INTERVAL - (time.time() - t0)))
