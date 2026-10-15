"""Historical LIP test data: per sampled paid-out program -> 1-min candles, trades, settlement.
Resumable (programs already in data.jsonl are skipped)."""
import json, os, random, time, urllib.request, urllib.error, datetime as dt, threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
B = "https://api.elections.kalshi.com/trade-api/v2"
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
LO, HI = P("2026-09-01T00:00:00Z"), P("2026-10-03T00:00:00Z")
def get(path):
    for a in range(7):
        try:
            with urllib.request.urlopen(B + path, timeout=60) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504): time.sleep(min(30, 2 ** a)); continue
            return None
        except Exception: time.sleep(min(30, 2 ** a))
    return None
meta = {s["ticker"]: s for s in json.load(open("series_all.json"))}
progs = [p for p in json.load(open("paid_out.json")) if p["incentive_type"] == "liquidity" and LO <= P(p["end_date"]) < HI]
bycat = defaultdict(list)
for p in progs: bycat[meta.get(p["market_ticker"].split("-")[0], {}).get("category", "?")].append(p)
top = sorted(bycat, key=lambda c: -len(bycat[c]))[:10]
rng = random.Random(11); sample = []
for c in top:
    ps = sorted(bycat[c], key=lambda p: p["id"]); rng.shuffle(ps); sample += [(c, p) for p in ps[:300]]
print("programs in window", len(progs), "categories", [(c, len(bycat[c])) for c in top], "sample", len(sample), flush=True)
done = set()
if os.path.exists("data.jsonl"):
    for l in open("data.jsonl"):
        try: done.add(json.loads(l)["id"])
        except Exception: pass
lock = threading.Lock(); out = open("data.jsonl", "a")
def fetch(item):
    cat, p = item
    if p["id"] in done: return
    t, s, e = p["market_ticker"], P(p["start_date"]), P(p["end_date"])
    m = (get(f"/markets/{t}") or {}).get("market") or {}
    rec = {"id": p["id"], "cat": cat, "ticker": t, "start": s, "end": e, "reward": p["period_reward"],
           "target": float(p.get("target_size_fp") or 0), "disc": p.get("discount_factor_bps"),
           "status": m.get("status"), "sv": m.get("settlement_value_dollars"), "close": m.get("close_time")}
    if rec["sv"] is not None and m.get("status") in ("settled", "finalized"):
        cs = []
        for a in range(s, e, 4000 * 60):
            d = get(f"/markets/candlesticks?market_tickers={t}&start_ts={a}&end_ts={min(e, a + 4000 * 60)}&period_interval=1") or {}
            for x in d.get("markets", []):
                cs += [[k["end_period_ts"], (k.get("yes_bid") or {}).get("close_dollars"), (k.get("yes_ask") or {}).get("close_dollars")] for k in x["candlesticks"]]
        tr, cur = [], ""
        while True:
            d = get(f"/markets/trades?ticker={t}&limit=1000&min_ts={s}&max_ts={e}" + (f"&cursor={cur}" if cur else "")) or {}
            tr += [[P(x["created_time"]), float(x["yes_price_dollars"]), float(x["count_fp"]), x["taker_side"]] for x in d.get("trades", [])]
            cur = d.get("cursor")
            if not cur or not d.get("trades"): break
        rec["candles"], rec["trades"] = cs, tr
    with lock: out.write(json.dumps(rec) + "\n"); out.flush()
n = 0
with ThreadPoolExecutor(8) as ex:
    for _ in ex.map(fetch, sample):
        n += 1
        if n % 250 == 0: print("programs", n, flush=True)
print("COLLECT DONE", flush=True)
