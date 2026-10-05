import json, os, time, random, urllib.request, urllib.error, datetime as dt
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
K = "https://api.elections.kalshi.com/trade-api/v2"
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
LO, HI = P("2026-08-21T00:00:00Z"), P("2026-10-05T00:00:00Z")
def get(url):
    for a in range(7):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504): time.sleep(min(30, 2 ** a)); continue
            return None
        except Exception: time.sleep(min(30, 2 ** a))
    return None
SER = {"KXBTC15M": "BTC-USD", "KXETH15M": "ETH-USD", "KXSOL15M": "SOL-USD"}
# 1) markets
if not os.path.exists("markets.json"):
    ms = []
    for s in SER:
        cur = ""
        while True:
            d = get(f"{K}/markets?series_ticker={s}&status=settled&limit=1000&min_close_ts={LO}&max_close_ts={HI}" + (f"&cursor={cur}" if cur else "")) or {}
            ms += [{k: m.get(k) for k in ("ticker", "open_time", "close_time", "floor_strike", "result", "settlement_value_dollars", "volume_fp")} for m in d.get("markets", [])]
            cur = d.get("cursor")
            if not cur or not d.get("markets"): break
    json.dump(ms, open("markets.json", "w")); print("markets", len(ms), flush=True)
ms = json.load(open("markets.json"))
# 2) spot
for s, prod in SER.items():
    fn = f"spot_{prod}.json"
    if os.path.exists(fn): continue
    out = {}
    for a in range(LO - 7200, HI, 300 * 60):
        b = min(HI, a + 300 * 60)
        d = get(f"https://api.exchange.coinbase.com/products/{prod}/candles?granularity=60&start={dt.datetime.utcfromtimestamp(a).isoformat()}Z&end={dt.datetime.utcfromtimestamp(b).isoformat()}Z") or []
        for c in d: out[c[0]] = [c[1], c[2], c[4]]   # [time, low, high, open, close, vol] -> low, high, close
        time.sleep(0.12)
    json.dump(out, open(fn, "w")); print("spot", prod, len(out), flush=True)
# 3) candles, batched by identical window
os.makedirs("cd", exist_ok=True)
win = defaultdict(list)
for m in ms: win[(P(m["open_time"]), P(m["close_time"]))].append(m["ticker"])
def fc(item):
    (o, c), tks = item
    fn = f"cd/{c}.json"
    if os.path.exists(fn): return
    d = get(f"{K}/markets/candlesticks?market_tickers={','.join(tks)}&start_ts={o}&end_ts={c}&period_interval=1") or {}
    res = {x["market_ticker"]: [[k["end_period_ts"], (k.get("yes_bid") or {}).get("close_dollars"), (k.get("yes_ask") or {}).get("close_dollars")] for k in x["candlesticks"]] for x in d.get("markets", [])}
    json.dump(res, open(fn, "w"))
with ThreadPoolExecutor(8) as ex: list(ex.map(fc, sorted(win.items())))
print("candles done", len(os.listdir("cd")), flush=True)
# 4) trades for S3 sample
os.makedirs("tr", exist_ok=True)
rng = random.Random(21); samp = []
for s in SER:
    x = sorted(m["ticker"] for m in ms if m["ticker"].startswith(s + "-")); rng.shuffle(x); samp += x[:200]
mm = {m["ticker"]: m for m in ms}
def ft(t):
    fn = f"tr/{t}.json"
    if os.path.exists(fn): return
    o, c = P(mm[t]["open_time"]), P(mm[t]["close_time"]); tr, cur = [], ""
    while True:
        d = get(f"{K}/markets/trades?ticker={t}&limit=1000&min_ts={o}&max_ts={c}" + (f"&cursor={cur}" if cur else "")) or {}
        tr += [[P(x["created_time"]), float(x["yes_price_dollars"]), float(x["count_fp"]), x["taker_side"]] for x in d.get("trades", [])]
        cur = d.get("cursor")
        if not cur or not d.get("trades"): break
    json.dump(tr, open(fn, "w"))
with ThreadPoolExecutor(8) as ex: list(ex.map(ft, samp))
print("COLLECT DONE trades", len(os.listdir("tr")), flush=True)
