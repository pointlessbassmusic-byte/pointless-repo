"""Confirmation test, docs/LIP_MENTIONS_PREREG_2026-10-03.md. The simulation body is
copied from analyze.py unchanged (same quote, fill and P&L rules)."""
import json, math, bisect, sys
from collections import defaultdict
Q = 100.0
meta = {s["ticker"]: s for s in json.load(open("series_all.json"))}

def simulate(r):
    sv = float(r["sv"]); s0 = meta.get(r["ticker"].split("-")[0], {})
    mult = float(s0.get("fee_multiplier") or 1); mfee = s0.get("fee_type", "").startswith("quadratic_with")
    cs = sorted((c for c in r["candles"] if c[1] is not None and c[2] is not None), key=lambda c: c[0])
    tr = sorted(r["trades"]); tts = [t[0] for t in tr]
    lt = lo = 0.0; quoted = 0
    for i, (ts, b, a) in enumerate(cs):
        b, a = float(b), float(a)
        if not (0 < b < a < 1) or a - b > 0.10: continue
        nxt = cs[i + 1][0] if i + 1 < len(cs) else ts + 60
        nxt = min(nxt, ts + 60)
        win = tr[bisect.bisect_right(tts, ts):bisect.bisect_right(tts, nxt)]
        quoted += 1
        for side, px in (("yes", b), ("no", 1 - a)):
            thr = tch = False
            for t, yp, n, taker in win:
                if side == "yes" and taker == "no":
                    thr |= yp < px - 1e-9; tch |= yp <= px + 1e-9
                if side == "no" and taker == "yes":
                    thr |= (1 - yp) < px - 1e-9; tch |= (1 - yp) <= px + 1e-9
            fee = 0.0175 * mult * px * (1 - px) if mfee else 0.0
            pnl = Q * ((sv if side == "yes" else 1 - sv) - px - fee)
            if thr: lt -= pnl
            if tch: lo -= pnl
    return quoted, -lt, -lo     # profits (positive = fills made money)

def tstat(xs):
    n = len(xs)
    if n < 2: return 0.0
    mu = sum(xs) / n; sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))
    return mu / (sd / math.sqrt(n)) if sd else 0.0

rows = []
for l in open(sys.argv[1] if len(sys.argv) > 1 else "mentions.jsonl"):
    r = json.loads(l)
    if r.get("sv") is None or not r.get("candles"): continue
    q, pt, po = simulate(r)
    if q: rows.append((r["ticker"].split("-")[0], r["end"] // 86400, pt, po, r["reward"] * 1e-4))
def report(name, rs):
    if len(rs) < 2: print(f"{name}: n={len(rs)}"); return None
    byd = defaultdict(list)
    for x in rs: byd[x[1]].append(x[2])
    dt_ = tstat([sum(v) / len(v) for v in byd.values()])
    tot_t, tot_o = sum(x[2] for x in rs), sum(x[3] for x in rs)
    t_ = tstat([x[2] for x in rs])
    print(f"{name}: programs {len(rs)} days {len(byd)} | through total ${tot_t:,.0f} t={t_:.2f} day_t={dt_:.2f} | touch total ${tot_o:,.0f} | pool ${sum(x[4] for x in rs):,.0f}")
    return tot_t, t_, dt_, tot_o
a = report("ALL mentions", rows)
b = report("excluding KXTRUMP*", [x for x in rows if not x[0].startswith("KXTRUMP")])
c = report("KXTRUMP* only", [x for x in rows if x[0].startswith("KXTRUMP")])
ok_all = a and a[0] > 0 and a[1] >= 2 and a[2] >= 2 and a[3] > 0
ok_ex = b and b[0] > 0
print("\nPASS (criteria 1-3)" if ok_all and ok_ex else "FAIL on criteria 1-3",
      "| narrower Trump-only:", "pass" if c and c[0] > 0 and c[1] >= 2 and c[2] >= 2 else "fail")
from collections import Counter
s = Counter()
for x in rows: s[x[0]] += x[2]
print("by series (through):", [(k, round(v)) for k, v in s.most_common(5)], "...", [(k, round(v)) for k, v in s.most_common()[-3:]])
