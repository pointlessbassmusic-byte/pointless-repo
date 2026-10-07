"""Descriptive (not a test): per category, settle rate vs quoted mid by bucket, all
fractions pooled, one obs per event per bucket. Shows where favourite-longshot bias
lives and how big it is next to the spread+fee toll."""
import json, datetime as dt, bisect
from collections import defaultdict
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
meta = {s["ticker"]: s for s in json.load(open("series_all.json"))}
B = (0.03, 0.15, 0.35, 0.65, 0.85, 0.97)
acc = defaultdict(lambda: defaultdict(list))
SEL = set(json.load(open("selected.json")))
for line in open("candles.jsonl"):
    r = json.loads(line)
    if r["event"] not in SEL: continue
    cat = meta.get(r["series"], {}).get("category", "?")
    for m in r["markets"]:
        cs = m["candles"]
        if not cs or m.get("settlement_value_dollars") is None: continue
        te = m.get("expected_expiration_time")
        if not te and m.get("can_close_early"): continue
        o, close = P(m["open_time"]), P(m["close_time"]); T = P(te) if te else close
        ts = [c[0] for c in cs]
        for f in (0.5, 0.75, 0.9, 0.98):
            t = o + f * (T - o)
            if t >= close: continue
            i = bisect.bisect_right(ts, t) - 1
            if i < 0: continue
            b, a = float(cs[i][1]), float(cs[i][2])
            if not (0 < b < a < 1) or a - b > 0.10: continue
            mid = (a + b) / 2
            for k in range(5):
                if B[k] <= mid < B[k + 1] or (k == 4 and mid == 0.97):
                    acc[(cat, k)][r["event"]].append((mid, float(m["settlement_value_dollars"]), a - b))
cats = sorted({c for c, _ in acc})
print("category | bucket | events | mean mid | settle rate | gap | mean spread")
for c in cats:
    for k in range(5):
        ev = acc.get((c, k))
        if not ev or len(ev) < 50: continue
        xs = [tuple(sum(v[j] for v in vals) / len(vals) for j in range(3)) for vals in ev.values()]
        n = len(xs); mm = sum(x[0] for x in xs) / n; sr = sum(x[1] for x in xs) / n; sp = sum(x[2] for x in xs) / n
        print(f"{c} | {B[k]:.2f}-{B[k+1]:.2f} | {n} | {mm:.3f} | {sr:.3f} | {sr-mm:+.3f} | {sp:.3f}")
