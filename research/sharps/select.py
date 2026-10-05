"""Period-A selection of sharps + placebo (docs/SHARPS_PREREG_2026-10-05.md)."""
import json, os, math, random
from collections import defaultdict
mk = {c: m for c, m in json.load(open("markets.json")).items() if m["vol"] >= 1e4}
def wallet_markets(rows):
    per = defaultdict(lambda: {"buy": 0.0, "sell": 0.0, "sh": defaultdict(float), "outs": set()})
    merge = False
    for r in rows:
        if r["type"] == "MERGE": merge = True; continue
        if r["type"] != "TRADE" or r["conditionId"] not in mk: continue
        p = per[r["conditionId"]]; usd = float(r["usdcSize"] or 0); sz = float(r["size"] or 0); o = int(r["outcomeIndex"])
        if r["side"] == "BUY": p["buy"] += usd; p["sh"][o] += sz; p["outs"].add(o)
        elif r["side"] == "SELL": p["sell"] += usd; p["sh"][o] -= sz
    pnl = {c: -p["buy"] + p["sell"] + sum(s * mk[c]["payout"][o] for o, s in p["sh"].items()) for c, p in per.items() if p["buy"] > 0}
    both = sum(1 for p in per.values() if len(p["outs"]) > 1) / max(1, len(per))
    return pnl, merge, both
def tstat(xs):
    n = len(xs)
    if n < 2: return 0.0
    mu = sum(xs) / n; sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))
    return mu / (sd / math.sqrt(n)) if sd else 0.0
eligible, sharps, mm = [], [], 0
for fn in os.listdir("actA"):
    w = fn[:-5]; d = json.load(open("actA/" + fn))
    pnl, merge, both = wallet_markets(d["rows"])
    if merge or both > 0.30: mm += 1; continue
    if len(pnl) < 20: continue
    xs = list(pnl.values()); t = tstat(xs)
    eligible.append(w)
    if t >= 2: sharps.append((w, len(xs), round(sum(xs)), round(t, 2)))
rng = random.Random(5)
pool = sorted(set(eligible) - {s[0] for s in sharps}); rng.shuffle(pool)
placebo = pool[:max(len(sharps), 1)]
json.dump({"sharps": [s[0] for s in sharps], "placebo": placebo}, open("sets.json", "w"))
json.dump([s[0] for s in sharps] + placebo, open("test_wallets.json", "w"))
print("wallets analysed", len(os.listdir("actA")), "excluded as market makers", mm, "eligible (>=20 mkts)", len(eligible), "sharps (t>=2)", len(sharps))
for s in sorted(sharps, key=lambda s: -s[2])[:15]: print("  ", s)
