"""Amendment 2 of docs/PINNACLE_TENNIS_PREREG_2026-10-08.md: sample Jan 1 - Aug 1 2026
KXATPMATCH/KXWTAMATCH markets from /historical/markets and pull their trade tapes.
Run in a scratch dir; writes hist_markets.json, kalshi_meta_cache.json (run.py's cache) and tr_hist/."""
import datetime as dt
import json
import os
import random
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

B = "https://api.elections.kalshi.com/trade-api/v2"
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def g(u):
    for a in range(10):
        try:
            return json.load(urllib.request.urlopen(u, timeout=60))
        except Exception:
            time.sleep(min(30, 1.5 ** a))
    return None


if not os.path.exists("hist_markets.json"):
    out = []
    for s in ("KXATPMATCH", "KXWTAMATCH"):
        cur = ""
        while True:
            d = g(f"{B}/historical/markets?series_ticker={s}&limit=1000" + (f"&cursor={cur}" if cur else "")) or {}
            out += [{k: m.get(k) for k in ("ticker", "close_time", "settlement_value_dollars", "volume_fp", "yes_sub_title", "title")}
                    for m in d.get("markets", [])]
            cur = d.get("cursor")
            if not cur or not d.get("markets"):
                break
    json.dump(out, open("hist_markets.json", "w"))
ms = [m for m in json.load(open("hist_markets.json"))
      if "2026-01-01" <= m["close_time"][:10] <= "2026-08-01" and float(m["volume_fp"] or 0) > 0
      and m["settlement_value_dollars"] is not None]
ms.sort(key=lambda m: m["ticker"])
rng = random.Random(7)
by_ev = {}
for m in ms:
    by_ev.setdefault(m["ticker"].rsplit("-", 1)[0], []).append(m)
picked = [rng.choice(v) for _, v in sorted(by_ev.items())]       # one random side per event
rng.shuffle(picked)
picked = picked[:int(os.environ.get("N", "1600"))]   # amendment 3: N=all events
print("sides in window", len(ms), "events", len(by_ev), "sampled", len(picked), flush=True)
json.dump({m["ticker"]: {"yes": m["yes_sub_title"], "title": m["title"], "close": m["close_time"],
                         "sv": m["settlement_value_dollars"]} for m in ms}, open("kalshi_meta_cache.json", "w"))
os.makedirs("tr_hist", exist_ok=True)


def ft(m):
    fn = f"tr_hist/{m['ticker']}.json"
    if os.path.exists(fn):
        return
    rows, cur = [], ""
    while True:
        d = g(f"{B}/historical/trades?ticker={m['ticker']}&limit=1000" + (f"&cursor={cur}" if cur else ""))
        if d is None:
            return                                   # failed pull: leave missing, never write a partial tape
        rows += [[P(x["created_time"]), float(x["yes_price_dollars"]), float(x["count_fp"]), x["taker_side"]] for x in d.get("trades", [])]
        cur = d.get("cursor")
        if not cur or not d.get("trades"):
            break
    json.dump(sorted(rows), open(fn, "w"))


with ThreadPoolExecutor(4) as ex:
    list(ex.map(ft, picked))
print("HIST COLLECT DONE", len(os.listdir("tr_hist")), "of", len(picked), flush=True)
