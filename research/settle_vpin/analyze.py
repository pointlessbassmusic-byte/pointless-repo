"""docs/SETTLE_VPIN_PREREG_2026-10-07.md"""
import json, os, math, sys, datetime as dt
from collections import defaultdict
sys.path.insert(0, "/home/user/pointless-repo")
from sportsbot.backtest.markout import prematch_cut, Fill
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
SPLIT = P("2026-09-05T00:00:00Z")
GROUP = {"KXATPMATCH": "ATP/WTA", "KXWTAMATCH": "ATP/WTA", "KXATPCHALLENGERMATCH": "Challenger/ITF", "KXITFMATCH": "Challenger/ITF", "KXMLBGAME": "MLB"}
FEE = {"KXATPMATCH": 0.0175, "KXWTAMATCH": 0.0175, "KXATPCHALLENGERMATCH": 0.0, "KXITFMATCH": 0.0, "KXMLBGAME": 0.0175 * 0.5}
import inspect
fill_fields = list(inspect.signature(Fill).parameters)
obs = defaultdict(list)     # (group, vpin_bin, half) -> [(market_mean, contracts)]
allfills = defaultdict(list); sides = defaultdict(lambda: [0, 0]); skipped = defaultdict(int)
for m in json.load(open("markets.json")):
    fn = f"tr/{m['ticker']}.json"
    if not os.path.exists(fn): skipped["no_tape"] += 1; continue
    tr = json.load(open(fn))
    if len(tr) < 20: skipped["thin"] += 1; continue
    fills = [Fill(ts=t, price=yp, taker_book_side=side) for t, yp, n, side in tr]   # prematch_cut reads price only
    cut = prematch_cut(fills)
    if cut is None: skipped["no_cut"] += 1; continue
    pre = tr[:cut]
    if len(pre) < 20: skipped["short_pre"] += 1; continue
    sv = float(m["settlement_value_dollars"]); s = m["series"]; grp = GROUP[s]; rate = FEE[s]
    half = "H1" if P(m["close_time"]) < SPLIT else "H2"
    V = sum(n for _, _, n, _ in pre) / 50.0
    buckets, cur_v, cur_imb = [], 0.0, 0.0
    per = defaultdict(lambda: [0.0, 0.0])
    for t, yp, n, side in pre:
        k = buckets[-20:]
        vp = sum(k) / 20 if len(k) == 20 else None
        b = "warmup" if vp is None else ("<0.86" if vp < 0.86 else ">=0.86")
        if side == "yes":   # maker bought NO at 1-yp
            p = 1 - yp; pay = 1 - sv; sides[grp][0] += n
        else:               # maker bought YES at yp
            p = yp; pay = sv; sides[grp][1] += n
        fee = math.ceil(rate * n * p * (1 - p) * 100 - 1e-9) / 100.0 if rate else 0.0
        pnl = n * (pay - p) - fee
        per[b][0] += pnl; per[b][1] += n
        allfills[grp].append(pnl)
        # update buckets (YES-taker volume minus NO-taker volume)
        rem = n
        while rem > 0:
            take = min(rem, V - cur_v); cur_v += take; cur_imb += take if side == "yes" else -take; rem -= take
            if cur_v >= V - 1e-9: buckets.append(abs(cur_imb) / V); cur_v, cur_imb = 0.0, 0.0
    for b, (pn, n) in per.items():
        if n > 0: obs[(grp, b, half)].append(pn / n)
print("skipped:", dict(skipped))
def st(xs):
    n = len(xs)
    if n < 2: return n, 0.0, 0.0
    mu = sum(xs) / n; sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))
    return n, mu, (mu / (sd / math.sqrt(n)) if sd else 0.0)
for grp in ("ATP/WTA", "Challenger/ITF", "MLB"):
    print(f"\n== {grp}  (taker YES:NO contracts = {sides[grp][0]:.0f}:{sides[grp][1]:.0f})")
    res = {}
    for b in ("<0.86", ">=0.86", "warmup"):
        line = []
        for h in ("H1", "H2"):
            n, mu, t = st(obs[(grp, b, h)]); res[(b, h)] = (n, mu, t)
            line.append(f"{h}: markets={n:3d} mean={mu * 100:+.2f}c/contract t={t:+.2f}")
        print(f"  VPIN {b:7s} " + " | ".join(line))
    a, c = res[("<0.86", "H1")], res[("<0.86", "H2")]
    verdict = "POOL EXISTS (pass)" if a[1] > 0 and a[2] >= 2 and c[1] > 0 and c[2] >= 2 else ("DEAD (<=0 both halves)" if a[1] <= 0 and c[1] <= 0 else "inconclusive")
    f = sorted(allfills[grp], reverse=True); top = sum(f[:max(1, len(f) // 50)]); tot = sum(f)
    print(f"  verdict: {verdict}; fills {len(f)}, total P&L ${tot:,.0f}, top-2% fills ${top:,.0f}")
