"""docs/SETTLE_ATPWTA_PREREG_2026-10-08.md"""
import json, os, math, sys, datetime as dt
from collections import defaultdict
sys.path.insert(0, "/home/user/pointless-repo")
from sportsbot.backtest.markout import prematch_cut, Fill
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
per, per_trim, days, allf, sides, skipped = [], [], [], [], [0, 0], defaultdict(int)
for m in json.load(open("markets2.json")):
    fn = f"tr2/{m['ticker']}.json"
    if not os.path.exists(fn): skipped["no_tape"] += 1; continue
    tr = json.load(open(fn))
    if len(tr) < 20: skipped["thin"] += 1; continue
    cut = prematch_cut([Fill(ts=t, price=yp, taker_book_side=s) for t, yp, n, s in tr])
    if cut is None or cut < 20: skipped["no_cut_or_short"] += 1; continue
    sv = float(m["settlement_value_dollars"]); pn = nn = 0.0; fl = []
    for t, yp, n, side in tr[:cut]:
        if side == "yes": p, pay = 1 - yp, 1 - sv; sides[0] += n
        else: p, pay = yp, sv; sides[1] += n
        fee = math.ceil(0.0175 * n * p * (1 - p) * 100 - 1e-9) / 100.0
        x = n * (pay - p) - fee; pn += x; nn += n; fl.append((x, n))
    if nn <= 0: continue
    per.append(pn / nn); days.append((P(m["close_time"]) // 86400, pn / nn)); allf += fl
def st(xs):
    n = len(xs); mu = sum(xs) / n; sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1)); return n, mu, mu / (sd / math.sqrt(n))
n, mu, t = st(per)
byd = defaultdict(list)
for d, x in days: byd[d].append(x)
_, dmu, dt_ = st([sum(v) / len(v) for v in byd.values()])
srt = sorted(allf, key=lambda f: -f[0]); k = max(1, len(srt) // 50)
trim = sum(x for x, _ in srt[k:]) / sum(nn for _, nn in srt[k:])
print("skipped", dict(skipped), "| taker YES:NO contracts", round(sides[0]), round(sides[1]))
print(f"markets {n}  mean maker P&L {mu * 100:+.2f}c/contract  market t {t:+.2f}  | days {len(byd)} day-clustered mean {dmu * 100:+.2f}c t {dt_:+.2f}")
print(f"contract-weighted P&L without the top 2% of fills: {trim * 100:+.2f}c/contract")
print("VERDICT:", "PASS" if mu > 0 and t >= 2 and dt_ >= 2 else "FAIL")
