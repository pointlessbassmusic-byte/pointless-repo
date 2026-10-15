import json, os, time, urllib.request, urllib.error, datetime as dt
from concurrent.futures import ThreadPoolExecutor
B = "https://api.elections.kalshi.com/trade-api/v2"
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
LO, HI = P("2026-08-05T00:00:00Z"), P("2026-10-06T00:00:00Z")
def g(u):
    for a in range(8):
        try: return json.load(urllib.request.urlopen(u, timeout=60))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504): time.sleep(min(30, 1.5 ** a)); continue
            return None
        except Exception: time.sleep(min(30, 1.5 ** a))
    return None
seen = {m["ticker"] for m in json.load(open("markets.json"))}
ms = []
for s in ("KXATPMATCH", "KXWTAMATCH"):
    cur = ""
    while True:
        d = g(f"{B}/markets?series_ticker={s}&status=settled&limit=1000&min_close_ts={LO}&max_close_ts={HI}" + (f"&cursor={cur}" if cur else "")) or {}
        ms += [{k: m.get(k) for k in ("ticker", "close_time", "settlement_value_dollars", "volume_fp")} | {"series": s} for m in d.get("markets", [])
               if m["ticker"] not in seen and m.get("settlement_value_dollars") is not None and float(m.get("volume_fp") or 0) > 0]
        cur = d.get("cursor")
        if not cur or not d.get("markets"): break
json.dump(ms, open("markets2.json", "w")); print("confirmation markets", len(ms), flush=True)
os.makedirs("tr2", exist_ok=True)
def ft(m):
    fn = f"tr2/{m['ticker']}.json"
    if os.path.exists(fn): return
    rows, cur = [], ""
    while True:
        d = g(f"{B}/markets/trades?ticker={m['ticker']}&limit=1000" + (f"&cursor={cur}" if cur else "")) or {}
        rows += [[P(x["created_time"]), float(x["yes_price_dollars"]), float(x["count_fp"]), x["taker_side"]] for x in d.get("trades", [])]
        cur = d.get("cursor")
        if not cur or not d.get("trades"): break
    json.dump(sorted(rows), open(fn, "w"))
with ThreadPoolExecutor(6) as ex: list(ex.map(ft, ms))
print("CONF COLLECT DONE", len(os.listdir("tr2")), flush=True)
