"""Collect: Kalshi daily-high settled markets (Jun-Sep 2026) + 60-min candles,
IEM ASOS tmpf per station, IEM CLI daily highs. Resumable per series/station."""
import json, os, re, time, urllib.request, urllib.error, datetime as dt
from concurrent.futures import ThreadPoolExecutor
K = "https://api.elections.kalshi.com/trade-api/v2"
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
LO, HI = P("2026-06-01T00:00:00Z"), P("2026-10-02T00:00:00Z")
def get(url, js=True):
    for a in range(7):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=90) as r:
                b = r.read(); return json.loads(b) if js else b.decode()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504): time.sleep(min(30, 2 ** a)); continue
            return None
        except Exception: time.sleep(min(30, 2 ** a))
    return None
series = [s for s in json.load(open("series_all.json")) if s["ticker"].startswith("KXHIGH") and s.get("frequency") == "daily"]
os.makedirs("mk", exist_ok=True)
def markets(s):
    out, seen = [], set()
    for base in (f"/markets?series_ticker={s}&status=settled&", f"/historical/markets?series_ticker={s}&"):
        cur = ""
        while True:
            d = get(K + base + "limit=1000" + (f"&cursor={cur}" if cur else ""))
            if not d: break
            for m in d.get("markets", []):
                if m["ticker"] in seen: continue
                seen.add(m["ticker"])
                if LO <= P(m["close_time"]) <= HI: out.append(m)
            cur = d.get("cursor")
            if not cur or not d.get("markets"): break
    return out
def code(ms):
    for m in sorted(ms, key=lambda m: m["close_time"], reverse=True):
        x = re.search(r"\(CLI([A-Z]{3})\)", m.get("rules_primary") or "")
        if x: return x.group(1)
    return None
def do_series(s):
    fn = f"mk/{s['ticker']}.json"
    if os.path.exists(fn): return
    ms = markets(s["ticker"])
    keep = ("ticker", "event_ticker", "open_time", "close_time", "result", "settlement_value_dollars",
            "strike_type", "floor_strike", "cap_strike", "rules_primary", "volume_fp")
    json.dump({"series": s["ticker"], "mult": s.get("fee_multiplier", 1), "code": code(ms),
               "markets": [{k: m.get(k) for k in keep} for m in ms]}, open(fn, "w"))
    print("series", s["ticker"], len(ms), code(ms), flush=True)
with ThreadPoolExecutor(4) as ex: list(ex.map(do_series, series))
print("MARKETS DONE", flush=True)
