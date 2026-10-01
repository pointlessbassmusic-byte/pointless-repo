"""Backfill candles for selected events whose markets got none from the batch endpoint
(settled before the historical cutoff). Per-market /historical/.../candlesticks;
same period rule and same compact() as collect_candles.py. Rewrites candles.jsonl."""
import json, time, urllib.request, urllib.error, datetime as dt
from concurrent.futures import ThreadPoolExecutor
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
B = "https://api.elections.kalshi.com/trade-api/v2"
src = open("collect_candles.py").read()
ns = {"P": P}
exec(src[src.index("POINTS ="):src.index("lock = threading.Lock()")], ns)
compact = ns["compact"]
SEL = set(json.load(open("selected.json")))

def get(p):
    for a in range(7):
        try:
            with urllib.request.urlopen(B + p, timeout=60) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504): time.sleep(min(30, 2 ** a)); continue
            return {}
        except Exception: time.sleep(min(30, 2 ** a))
    return {}

recs = [json.loads(l) for l in open("candles.jsonl")]
todo = [r for r in recs if r["event"] in SEL and r["markets"] and not any(m["candles"] for m in r["markets"])]
print("events to backfill", len(todo), flush=True)

def fill(r):
    o = min(P(m["open_time"]) for m in r["markets"]); c = max(P(m["close_time"]) for m in r["markets"])
    per = r["period"]; o = max(o, c - 4900 * per * 60)
    for m in r["markets"]:
        d = get(f"/historical/markets/{m['ticker']}/candlesticks?start_ts={o}&end_ts={c}&period_interval={per}")
        cs = [[k["end_period_ts"], (k.get("yes_bid") or {}).get("close"), (k.get("yes_ask") or {}).get("close"),
               float(k.get("volume") or 0)] for k in d.get("candlesticks", [])]
        m["candles"] = compact(m, cs)
    return r

n = 0
with ThreadPoolExecutor(10) as ex:
    for _ in ex.map(fill, todo):
        n += 1
        if n % 1000 == 0: print("filled", n, flush=True)
with open("candles.tmp", "w") as f:
    for r in recs: f.write(json.dumps(r) + "\n")
import os; os.replace("candles.tmp", "candles.jsonl")
print("BACKFILL DONE", flush=True)
