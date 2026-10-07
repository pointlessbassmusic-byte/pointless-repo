import json, os, time, urllib.request, urllib.error, datetime as dt
from concurrent.futures import ThreadPoolExecutor
H = {"User-Agent": "Mozilla/5.0"}
K = "https://api.elections.kalshi.com/trade-api/v2"
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T").replace("+00:00:00", "+00:00")).timestamp())
def g(u):
    for a in range(7):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=60))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504): time.sleep(min(30, 2 ** a)); continue
            return None
        except Exception: time.sleep(min(30, 2 ** a))
    return None
pm = json.load(open("markets.json"))
pm = {c: v for c, v in pm.items() if "mlb" in v["tags"] and v["end"].startswith("2026-09") and v.get("start")}
# Kalshi MLB game markets in Sep
lo, hi = P("2026-09-01T00:00:00Z"), P("2026-10-02T00:00:00Z")
km, cur = [], ""
while True:
    d = g(f"{K}/markets?series_ticker=KXMLBGAME&status=settled&limit=1000&min_close_ts={lo}&max_close_ts={hi}" + (f"&cursor={cur}" if cur else "")) or {}
    km += d.get("markets", []); cur = d.get("cursor")
    if not cur or not d.get("markets"): break
print("polymarket MLB", len(pm), "kalshi MLB markets", len(km), flush=True)
norm = lambda s: (s or "").lower().replace(".", "").strip()
def pm_start(v):
    s = v["start"].replace(" ", "T")
    if s.endswith("+00"): s += ":00"
    return int(dt.datetime.fromisoformat(s).timestamp())
games = []
for c, v in pm.items():
    t0 = pm_start(v); team0 = norm(v["outcomes"][0])
    cands = [m for m in km if norm(m.get("yes_sub_title")) and (norm(m.get("yes_sub_title")) in team0 or team0 in norm(m.get("yes_sub_title")))]
    # Kalshi ticker carries ET start time; use occurrence/expected expiration minus ~3h as proxy, require within 3h
    best = None
    for m in cands:
        ke = P(m.get("expected_expiration_time") or m["close_time"]) - 3 * 3600
        if abs(ke - t0) <= 3 * 3600 and (best is None or abs(ke - t0) < best[0]): best = (abs(ke - t0), m)
    if best:
        m = best[1]
        games.append({"pm": c, "q": v["q"], "start": t0, "tokens": v["tokens"], "payout": v["payout"], "k": m["ticker"], "k_team": m.get("yes_sub_title"),
                      "k_sv": m.get("settlement_value_dollars"), "k_close": P(m["close_time"])})
json.dump(games, open("games.json", "w")); print("matched games", len(games), flush=True)
os.makedirs("pmt", exist_ok=True); os.makedirs("kt", exist_ok=True)
def pm_trades(gm):
    fn = f"pmt/{gm['pm']}.json"
    if os.path.exists(fn): return
    rows, end = [], None
    for _ in range(200):
        d = g(f"https://data-api.polymarket.com/trades?market={gm['pm']}&limit=500" + (f"&end={end}" if end else ""))
        if not d: break
        rows += [[x["timestamp"], x["outcomeIndex"], float(x["price"]), float(x["size"]), x["side"]] for x in d]
        if len(d) < 500: break
        end = min(x["timestamp"] for x in d) - 1
        if end < gm["start"] - 6 * 3600: break
    json.dump(rows, open(fn, "w"))
def k_trades(gm):
    fn = f"kt/{gm['k']}.json"
    if os.path.exists(fn): return
    rows, cur = [], ""
    while True:
        d = g(f"{K}/markets/trades?ticker={gm['k']}&limit=1000&min_ts={gm['start'] - 6 * 3600}" + (f"&cursor={cur}" if cur else "")) or {}
        rows += [[P(x["created_time"]), float(x["yes_price_dollars"]), float(x["count_fp"]), x["taker_side"]] for x in d.get("trades", [])]
        cur = d.get("cursor")
        if not cur or not d.get("trades"): break
    json.dump(rows, open(fn, "w"))
with ThreadPoolExecutor(8) as ex:
    list(ex.map(pm_trades, games)); list(ex.map(k_trades, games))
print("LAG COLLECT DONE", flush=True)
