"""EXPLORATORY decomposition of the copy gap (not a pre-registered test)."""
import json, math
from collections import defaultdict
exec(open("copytest.py").read().split("def evaluate")[0])
sig = signals(sets["sharps"]); items = list(sig.items())
from concurrent.futures import ThreadPoolExecutor
def hist(kv):
    c, r = kv
    d = g(f"https://clob.polymarket.com/prices-history?market={r['asset']}&startTs={r['timestamp'] - 600}&endTs={r['timestamp'] + 900}&fidelity=1") or {}
    return sorted(d.get("history", []), key=lambda x: x["t"])
with ThreadPoolExecutor(8) as ex: H = list(ex.map(hist, items))
drift, before, rows = [], [], defaultdict(list)
for (c, r), h in zip(items, H):
    t, theirs = r["timestamp"], float(r["price"]); pay = mk[c]["payout"][int(r["outcomeIndex"])]
    pre = [x["p"] for x in h if x["t"] <= t - 60]; post = {lag: next((x["p"] for x in h if x["t"] >= t + lag), None) for lag in (60, 300, 900)}
    if pre: before.append(theirs - pre[-1])
    for lag, p in post.items():
        if p is None: continue
        drift.append((lag, p - theirs))
        rows[f"mid at +{lag}s, no extra slippage"].append(pay - p - fee(p))
    if pre: rows["mid 1 min BEFORE their trade (impossible; reference)"].append(pay - pre[-1] - fee(pre[-1]))
    rows["their price"].append(pay - theirs - fee(theirs))
print("their price minus mid 1 min before: mean %+.4f (n=%d)" % (sum(before) / len(before), len(before)))
for lag in (60, 300, 900):
    d = [x for l, x in drift if l == lag]; print(f"mid at +{lag}s minus their price: mean {sum(d) / len(d):+.4f} (n={len(d)})")
for k, v in rows.items():
    mu = sum(v) / len(v); sd = math.sqrt(sum((x - mu) ** 2 for x in v) / (len(v) - 1)); print(f"{k:55s} n={len(v)} mean={mu:+.4f} t={mu / (sd / math.sqrt(len(v))):+.2f}")
# timing relative to game start
early = defaultdict(list)
import datetime as dt
for (c, r) in items:
    st = mk[c].get("start")
    if not st: continue
    try: s = dt.datetime.fromisoformat(st.replace(" ", "T").replace("+00", "+00:00")).timestamp()
    except Exception: continue
    k = "in-play" if r["timestamp"] >= s else ">6h pre" if s - r["timestamp"] > 6 * 3600 else "0-6h pre"
    pay = mk[c]["payout"][int(r["outcomeIndex"])]; early[k].append(pay - float(r["price"]) - fee(float(r["price"])))
print({k: (len(v), round(sum(v) / len(v), 4)) for k, v in early.items()})
