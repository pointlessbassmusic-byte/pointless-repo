import json, os, random, time, urllib.request, urllib.error, datetime as dt, threading
from concurrent.futures import ThreadPoolExecutor
B = "https://api.elections.kalshi.com/trade-api/v2"
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
LO, HI = P("2026-06-01T00:00:00Z"), P("2026-09-01T00:00:00Z")
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
progs = [p for p in json.load(open("paid_out.json")) if p["incentive_type"] == "liquidity" and LO <= P(p["end_date"]) < HI
         and meta.get(p["market_ticker"].split("-")[0], {}).get("category") == "Mentions"]
progs.sort(key=lambda p: p["id"]); rng = random.Random(12); rng.shuffle(progs); sample = progs[:400]
print("mentions programs Jun-Aug", len(progs), "sample", len(sample), flush=True)
done = set()
if os.path.exists("mentions.jsonl"):
    done = {json.loads(l)["id"] for l in open("mentions.jsonl")}
lock = threading.Lock(); out = open("mentions.jsonl", "a")
def fetch(p):
    if p["id"] in done: return
    t, s, e = p["market_ticker"], P(p["start_date"]), P(p["end_date"])
    m = (get(f"/markets/{t}") or {}).get("market")
    hist = False
    if not m:
        m = (get(f"/historical/markets/{t}") or {}).get("market") or {}; hist = True
    rec = {"id": p["id"], "cat": "Mentions", "ticker": t, "start": s, "end": e, "reward": p["period_reward"],
           "status": m.get("status"), "sv": m.get("settlement_value_dollars"), "hist": hist}
    if rec["sv"] is not None:
        cs = []
        for a in range(s, e, 4000 * 60):
            b = min(e, a + 4000 * 60)
            if hist:
                d = get(f"/historical/markets/{t}/candlesticks?start_ts={a}&end_ts={b}&period_interval=1") or {}
                cs += [[k["end_period_ts"], (k.get("yes_bid") or {}).get("close"), (k.get("yes_ask") or {}).get("close")] for k in d.get("candlesticks", [])]
            else:
                d = get(f"/markets/candlesticks?market_tickers={t}&start_ts={a}&end_ts={b}&period_interval=1") or {}
                for x in d.get("markets", []):
                    cs += [[k["end_period_ts"], (k.get("yes_bid") or {}).get("close_dollars"), (k.get("yes_ask") or {}).get("close_dollars")] for k in x["candlesticks"]]
        tr = []
        for base in ("/markets/trades?", "/historical/trades?"):
            cur = ""
            while True:
                d = get(f"{base}ticker={t}&limit=1000&min_ts={s}&max_ts={e}" + (f"&cursor={cur}" if cur else "")) or {}
                tr += [[P(x["created_time"]), float(x["yes_price_dollars"]), float(x["count_fp"]), x["taker_side"]] for x in d.get("trades", [])]
                cur = d.get("cursor")
                if not cur or not d.get("trades"): break
            if tr: break
        rec["candles"], rec["trades"] = cs, [x for x in tr if s <= x[0] <= e]
    with lock: out.write(json.dumps(rec) + "\n"); out.flush()
with ThreadPoolExecutor(8) as ex: list(ex.map(fetch, sample))
print("MENTIONS DONE", flush=True)
