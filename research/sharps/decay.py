"""EXPLORATORY: how fast the sharps' in-play edge decays, from second-stamped trades."""
import json, math, random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
exec(open("copytest.py").read().split("def evaluate")[0])
sig = list(signals(sets["sharps"]).items()); random.Random(9).shuffle(sig); sig = sig[:700]
def trades_around(kv):
    c, r = kv; out = []
    for off in range(0, 2500, 500):
        d = g(f"https://data-api.polymarket.com/trades?market={c}&limit=500&offset={off}") or []
        out += d
        if len(d) < 500 or (d and d[-1]["timestamp"] < r["timestamp"] - 120): break
    return out
with ThreadPoolExecutor(8) as ex: TR = list(ex.map(trades_around, sig))
B = ((1, 5), (5, 15), (15, 30), (30, 60), (60, 120))
res = defaultdict(list); covered = 0
for (c, r), tr in zip(sig, TR):
    t, o = r["timestamp"], int(r["outcomeIndex"]); pay = mk[c]["payout"][o]
    if not tr or min(x["timestamp"] for x in tr) > t: continue
    covered += 1
    for lo, hi in B:
        # price a copier could pay: first BUY of the same outcome by someone else in the window
        nxt = sorted((x for x in tr if x["outcomeIndex"] == o and x["side"] == "BUY" and t + lo <= x["timestamp"] < t + hi and x["proxyWallet"] != r["wallet"]), key=lambda x: x["timestamp"])
        if nxt:
            p = float(nxt[0]["price"]); res[(lo, hi)].append(pay - p - fee(p))
print("signals with trade coverage at t:", covered, "of", len(sig))
for k in B:
    v = res[k]
    if len(v) > 2:
        mu = sum(v) / len(v); sd = math.sqrt(sum((x - mu) ** 2 for x in v) / (len(v) - 1))
        print(f"copy fill between +{k[0]:>3d}s and +{k[1]:>3d}s: n={len(v)} mean={mu:+.4f}/share t={mu / (sd / math.sqrt(len(v))):+.2f}")
