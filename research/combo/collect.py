import json, os, random, time, urllib.request, urllib.error, datetime as dt
from concurrent.futures import ThreadPoolExecutor
B = "https://api.elections.kalshi.com/trade-api/v2"
def g(u):
    for a in range(9):
        try: return json.load(urllib.request.urlopen(u, timeout=60))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504): time.sleep(min(30, 1.5 ** a)); continue
            return None
        except Exception: time.sleep(min(30, 1.5 ** a))
    return None
rng = random.Random(31)
day0 = dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc)
windows = []
for i in range(44):
    s = int((day0 + dt.timedelta(days=i)).timestamp()) + rng.randrange(0, 86400 - 120)
    windows.append(s)
def window(s):
    fn = f"w_{s}.json"
    if os.path.exists(fn): return json.load(open(fn))
    rows, cur = [], ""
    while True:
        d = g(f"{B}/markets/trades?limit=1000&min_ts={s}&max_ts={s + 120}" + (f"&cursor={cur}" if cur else "")) or {}
        rows += [x for x in d.get("trades", []) if x["ticker"].startswith("KXMVE")]
        cur = d.get("cursor")
        if not cur or not d.get("trades"): break
        time.sleep(0.15)
    samp = rows[:]; random.Random(s).shuffle(samp); samp = samp[:150]
    out = {"start": s, "n_mve": len(rows), "sample": samp}
    json.dump(out, open(fn, "w")); return out
W = []
for s in windows:
    W.append(window(s)); print("window", s, W[-1]["n_mve"], flush=True)
tick = sorted({x["ticker"] for w in W for x in w["sample"]})
res = json.load(open("results.json")) if os.path.exists("results.json") else {}
def mk(t):
    if t in res: return t, res[t]
    d = g(f"{B}/markets/{t}") or {}
    m = d.get("market") or {}
    if not m:
        m = (g(f"{B}/historical/markets/{t}") or {}).get("market") or {}
    return t, {"status": m.get("status"), "sv": m.get("settlement_value_dollars"), "title": (m.get("title") or "")[:300],
               "legs": len(((m.get("custom_strike") or {}).get("Associated Markets") or "").split(",")) if isinstance(m.get("custom_strike"), dict) else None}
with ThreadPoolExecutor(4) as ex:
    for t, v in ex.map(mk, tick): res[t] = v
json.dump(res, open("results.json", "w")); print("COMBO DONE tickers", len(tick), flush=True)
