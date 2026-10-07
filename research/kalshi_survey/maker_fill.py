"""Fill check for maker cells. Post 1 contract at the quote at entry time t, held to close.
YES: resting bid at b fills on a later trade where the taker sold YES (taker_side 'no')
at yes_price <= b. NO: resting at YES-ask a fills on a taker YES buy at yes_price >= a.
'through' = trade strictly through our price (fill certain regardless of queue);
'touch' = at-or-through (optimistic, ignores queue). P&L only on filled entries,
one obs per event. Usage: maker_fill.py CATEGORY SIDE BUCKET FRAC [max_events]"""
import json, sys, math, bisect, random, time, urllib.request, datetime as dt
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
NOW = 1790812800
BK = (0.03, 0.15, 0.35, 0.65, 0.85, 0.97)
cat, side, bk, f = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
cap = int(sys.argv[5]) if len(sys.argv) > 5 else 400
meta = {s["ticker"]: s for s in json.load(open("series_all.json"))}
SEL = set(json.load(open("selected.json")))
B = "https://api.elections.kalshi.com/trade-api/v2"

def get(p):
    for a in range(6):
        try:
            with urllib.request.urlopen(B + p, timeout=60) as r: return json.load(r)
        except Exception: time.sleep(2 ** a)
    return {}

def trades(tk, t0, t1):
    out = []
    for base in ("/markets/trades?", "/historical/trades?"):
        cur = ""
        while True:
            d = get(f"{base}ticker={tk}&limit=1000&min_ts={int(t0)}&max_ts={int(t1)}" + (f"&cursor={cur}" if cur else ""))
            out += d.get("trades", [])
            cur = d.get("cursor")
            if not cur or not d.get("trades"): break
        if out: break
    # historical endpoint may ignore min_ts/max_ts: enforce the window here
    return [x for x in out if t0 <= P(x["created_time"]) <= t1]

entries = []
for line in open("candles.jsonl"):
    r = json.loads(line)
    if r["event"] not in SEL: continue
    if (r["series"] != cat) if cat.startswith("KX") else (meta.get(r["series"], {}).get("category") != cat): continue
    for m in r["markets"]:
        cs = m["candles"]
        if not cs or m.get("settlement_value_dollars") is None: continue
        te = m.get("expected_expiration_time")
        if not te and m.get("can_close_early"): continue
        o, close = P(m["open_time"]), P(m["close_time"]); T = P(te) if te else close
        t = o + f * (T - o)
        if t >= close: continue
        i = bisect.bisect_right([c[0] for c in cs], t) - 1
        if i < 0: continue
        b, a = float(cs[i][1]), float(cs[i][2])
        if not (0 < b < a < 1) or a - b > 0.10: continue
        mid = (a + b) / 2
        if not (BK[bk] <= mid < BK[bk + 1] or (bk == 4 and mid == 0.97)): continue
        off = (NOW - close) // 86400 + 1
        entries.append((r["event"], off, m["ticker"], t, close, b, a, float(m["settlement_value_dollars"]),
                        meta[r["series"]].get("fee_type", "").startswith("quadratic_with"), float(meta[r["series"]].get("fee_multiplier") or 1)))
evs = sorted({e[0] for e in entries}); random.Random(3).shuffle(evs); keep = set(evs[:cap])
entries = [e for e in entries if e[0] in keep]

def run(e):
    ev, off, tk, t, close, b, a, sv, mf, mult = e
    tr = trades(tk, t, close)
    through = touch = False
    for x in tr:
        yp = float(x["yes_price_dollars"])
        if side == "YES" and x["taker_side"] == "no":
            through |= yp < b - 1e-9; touch |= yp <= b + 1e-9
        if side == "NO" and x["taker_side"] == "yes":
            through |= yp > a + 1e-9; touch |= yp >= a - 1e-9
    p = b if side == "YES" else 1 - a
    fee = 0.0175 * mult * p * (1 - p) if mf else 0.0
    pnl = (sv if side == "YES" else 1 - sv) - p - fee
    return ev, off, through, touch, pnl, p

with ThreadPoolExecutor(8) as ex: res = list(ex.map(run, entries))

def summ(name, rows):
    per = defaultdict(list)
    for ev, off, th, to, pnl, p in rows: per[ev].append((pnl, off))
    xs = [sum(v[0] for v in vals) / len(vals) for vals in per.values()]
    n = len(xs)
    if n < 2: print(name, "n", n); return
    mu = sum(xs) / n; sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))
    print(f"{name}: events {n} mean pnl/contract {mu:+.4f} t {mu / (sd / math.sqrt(n)) if sd else 0:.2f}")

print(cat, side, bk, f, "entries", len(res), "events", len({r[0] for r in res}))
summ("all (maker_ub, assumes fill)", res)
summ("filled-through (conservative)", [r for r in res if r[2]])
summ("filled-touch (optimistic)", [r for r in res if r[3]])
summ("unfilled-touch", [r for r in res if not r[3]])
print("fill rate through %.2f touch %.2f" % (sum(r[2] for r in res) / max(1, len(res)), sum(r[3] for r in res) / max(1, len(res))))
