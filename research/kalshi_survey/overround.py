"""Live scan of open mutually-exclusive Kalshi events.
NO-basket: buy NO on every market. At most one YES => payout >= k-1. Edge = sum(bid_yes) - 1 - fees.
YES-basket: buy YES on every market; pays 1 only if the set is exhaustive. Edge = 1 - sum(ask) - fees."""
import json, time, urllib.request, urllib.error
B = "https://api.elections.kalshi.com/trade-api/v2"
meta = {s["ticker"]: s for s in json.load(open("series_all.json"))}
def get(p):
    for a in range(6):
        try:
            with urllib.request.urlopen(B + p, timeout=60) as r: return json.load(r)
        except Exception: time.sleep(2 ** a)
cur, evs = "", []
while True:
    d = get("/events?status=open&with_nested_markets=true&limit=200" + (f"&cursor={cur}" if cur else ""))
    evs += d["events"]; cur = d.get("cursor")
    if not cur or not d["events"]: break
print("open events", len(evs), "mutually exclusive", sum(1 for e in evs if e.get("mutually_exclusive")))
out = []
for e in evs:
    if not e.get("mutually_exclusive"): continue
    ms = [m for m in e.get("markets", []) if m.get("status") in ("active", "open")]
    if len(ms) < 2: continue
    mult = float(meta.get(e["series_ticker"], {}).get("fee_multiplier") or 1)
    bid = [float(m.get("yes_bid_dollars") or 0) for m in ms]
    ask = [float(m.get("yes_ask_dollars") or 1) for m in ms]
    bsz = [float(m.get("yes_bid_size_fp") or 0) for m in ms]
    asz = [float(m.get("yes_ask_size_fp") or 0) for m in ms]
    # NO basket: buy NO at 1-bid on every market; markets with bid 0 cost 1.00 and are skipped only if NO is unbuyable
    no_px = [1 - b for b in bid]
    no_fee = sum(0.07 * mult * p * (1 - p) for p in no_px)
    no_edge = (len(ms) - 1) - sum(no_px) - no_fee
    no_size = min(bsz) if all(b > 0 for b in bid) else 0
    yes_fee = sum(0.07 * mult * p * (1 - p) for p in ask)
    yes_edge = 1 - sum(ask) - yes_fee
    yes_size = min(asz) if all(a < 1 for a in ask) else 0
    out.append((e["event_ticker"], len(ms), round(sum(bid), 4), round(sum(ask), 4), round(no_edge, 4), no_size, round(yes_edge, 4), yes_size))
pos_no = [o for o in out if o[4] > 0 and o[5] > 0]
pos_yes = [o for o in out if o[6] > 0 and o[7] > 0]
print("ME events scanned", len(out), "NO-basket edge>0 with size:", len(pos_no), "YES-basket edge>0 with size:", len(pos_yes))
for o in sorted(pos_no, key=lambda o: -o[4])[:20]: print("NO ", o)
for o in sorted(pos_yes, key=lambda o: -o[6])[:20]: print("YES", o)
json.dump(out, open(f"overround_{int(time.time())}.json", "w"))
