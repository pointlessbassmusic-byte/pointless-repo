"""docs/PM_TENNIS_BOOKS_PREREG_2026-10-08.md: Polymarket tennis moneylines Jan 1 - Aug 1 2026
and their pre-cut trade tapes. Run in a scratch dir; writes markets.json and pmt/<conditionId>.json
rows [ts, yes_price, size, taker_side_for_yes] with YES = outcome 0."""
import datetime as dt
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

H = {"User-Agent": "Mozilla/5.0"}
CUT_S = 1800
LO, HI = dt.date(2026, 1, 1), dt.date(2026, 8, 1)


def g(u):
    for a in range(8):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=60))
        except Exception:
            time.sleep(min(30, 2 ** a))
    return None


def ts(s):
    s = s.replace("Z", "+00:00").replace(" ", "T")
    if s.endswith("+00"):
        s += ":00"
    return int(dt.datetime.fromisoformat(s).timestamp())


if not os.path.exists("markets.json"):
    out, day = {}, LO
    while day <= HI + dt.timedelta(days=21):          # event endDate runs ~1 week past the match
        nxt, off = day + dt.timedelta(days=1), 0
        while True:
            d = g(f"https://gamma-api.polymarket.com/events?closed=true&limit=100&offset={off}&tag_slug=tennis"
                  f"&end_date_min={day}T00:00:00Z&end_date_max={nxt}T00:00:00Z") or []
            for e in d:
                for m in e.get("markets", []):
                    if m.get("sportsMarketType") != "moneyline" or not m.get("gameStartTime"):
                        continue
                    try:
                        pr = [float(x) for x in json.loads(m["outcomePrices"])]
                    except Exception:
                        continue
                    start = ts(m["gameStartTime"])
                    if sorted(pr) != [0.0, 1.0] or not (LO <= dt.date.fromtimestamp(start) < HI) or float(m.get("volume") or 0) < 1e4:
                        continue
                    out[m["conditionId"]] = {"q": m.get("question"), "slug": e.get("slug"), "start": start,
                                             "outcomes": json.loads(m["outcomes"]), "payout": pr, "vol": float(m["volume"])}
            if len(d) < 100:
                break
            off += 100
        day = nxt
        print(day, len(out), flush=True)
    json.dump(out, open("markets.json", "w"))
mk = json.load(open("markets.json"))
print("tennis moneylines in window", len(mk), flush=True)
os.makedirs("pmt", exist_ok=True)


def pm_trades(item):
    c, m = item
    fn = f"pmt/{c}.json"
    if os.path.exists(fn):
        return
    cut = m["start"] - CUT_S
    rows, end = [], cut
    for _ in range(100):
        d = g(f"https://data-api.polymarket.com/trades?market={c}&limit=500&end={end}")
        if d is None:
            return                                    # failed pull: never write a partial tape
        for x in d:
            p, side = float(x["price"]), x["side"]
            if x["outcomeIndex"] == 1:                # normalise to the YES (outcome 0) token
                p, side = 1 - p, {"BUY": "SELL", "SELL": "BUY"}[side]
            if x["timestamp"] <= cut:
                rows.append([x["timestamp"], p, float(x["size"]), side])
        if len(d) < 500:
            break
        end = min(x["timestamp"] for x in d) - 1
        if end < cut - 6 * 3600:
            break
    json.dump(sorted(rows), open(fn, "w"))


with ThreadPoolExecutor(6) as ex:
    list(ex.map(pm_trades, sorted(mk.items())))
print("PM COLLECT DONE", len(os.listdir("pmt")), "of", len(mk), flush=True)
