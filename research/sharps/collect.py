import json, os, sys, time, random, urllib.request, datetime as dt
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
H = {"User-Agent": "Mozilla/5.0"}
def g(u):
    for a in range(6):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=60))
        except Exception: time.sleep(min(20, 2 ** a))
    return None
E = lambda s: int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp())
A0, A1, B0, B1 = E("2026-08-01"), E("2026-09-01"), E("2026-09-01"), E("2026-10-01")
stage = sys.argv[1]
if stage == "markets":
    out = {}
    day = dt.date(2026, 8, 1)
    while day < dt.date(2026, 10, 1):
        nxt = day + dt.timedelta(days=1); off = 0
        while True:
            d = g(f"https://gamma-api.polymarket.com/events?closed=true&limit=100&offset={off}&tag_slug=sports&end_date_min={day}T00:00:00Z&end_date_max={nxt}T00:00:00Z") or []
            for e in d:
                for m in e.get("markets", []):
                    if m.get("sportsMarketType") != "moneyline": continue
                    try: pr = [float(x) for x in json.loads(m["outcomePrices"])]
                    except Exception: continue
                    if sorted(pr) != [0.0, 1.0]: continue
                    out[m["conditionId"]] = {"q": m.get("question"), "end": m.get("endDate"), "start": m.get("gameStartTime"),
                                             "outcomes": json.loads(m["outcomes"]), "payout": pr, "tokens": json.loads(m["clobTokenIds"]),
                                             "vol": float(m.get("volume") or 0), "tags": [t.get("slug") for t in e.get("tags", [])]}
            if len(d) < 100: break
            off += 100
        day = nxt
    json.dump(out, open("markets.json", "w")); print("moneylines", len(out), flush=True)
mk = json.load(open("markets.json")) if os.path.exists("markets.json") else {}
if stage != "markets":
    mk = {c: m for c, m in mk.items() if m["vol"] >= 1e4}   # amendment 1
def end_ts(m):
    try: return int(dt.datetime.fromisoformat(m["end"].replace("Z", "+00:00")).timestamp())
    except Exception: return 0
if stage == "pool":
    A = sorted(c for c, m in mk.items() if A0 <= end_ts(m) < A1 and m["vol"] >= 1e5)
    random.Random(13).shuffle(A); A = A[:800]
    vol = defaultdict(float)
    def f(c):
        rows = []
        for off in range(0, 2500, 500):
            d = g(f"https://data-api.polymarket.com/trades?market={c}&limit=500&offset={off}")
            if not d: break
            rows += d
            if len(d) < 500: break
        return rows
    with ThreadPoolExecutor(8) as ex:
        for rows in ex.map(f, A):
            for r in rows:
                if r.get("side") == "BUY": vol[r["proxyWallet"]] += float(r["size"]) * float(r["price"])
    cand = sorted([w for w, v in vol.items() if v >= 500], key=lambda w: -vol[w])[:1500]
    json.dump(cand, open("pool.json", "w")); print("A markets", len(A), "wallets seen", len(vol), "candidates", len(cand), flush=True)
def activity(w, lo, hi, maxpages=40):
    rows, end = [], hi
    for _ in range(maxpages):
        d = g(f"https://data-api.polymarket.com/activity?user={w}&limit=500&start={lo}&end={end}")
        if not d: break
        rows += d
        if len(d) < 500: break
        end = d[-1]["timestamp"] - 1
        if end < lo: break
    return rows
if stage in ("actA", "actB"):
    lo, hi = (A0, A1 + 3 * 86400) if stage == "actA" else (B0, B1 + 3 * 86400)
    ws = json.load(open("pool.json")) if stage == "actA" else json.load(open("test_wallets.json"))
    os.makedirs(stage, exist_ok=True)
    def f(w):
        fn = f"{stage}/{w}.json"
        if os.path.exists(fn): return
        rows = activity(w, lo, hi)
        keep = [{k: r.get(k) for k in ("timestamp", "type", "side", "conditionId", "outcomeIndex", "size", "usdcSize", "price", "asset")} for r in rows
                if r.get("type") == "MERGE" or r.get("conditionId") in mk]
        json.dump({"n_all": len(rows), "rows": keep}, open(fn, "w"))
    with ThreadPoolExecutor(8) as ex: list(ex.map(f, ws))
    print(stage, "done", len(os.listdir(stage)), flush=True)
