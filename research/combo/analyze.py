"""docs/COMBO_PREREG_2026-10-05.md"""
import json, glob, math, datetime as dt
from collections import defaultdict
res = json.load(open("results.json"))
SPLIT = int(dt.datetime(2026, 9, 11, tzinfo=dt.timezone.utc).timestamp())
rows = []; dropped = 0
for fn in glob.glob("w_*.json"):
    w = json.load(open(fn))
    for x in w["sample"]:
        r = res.get(x["ticker"]) or {}
        if r.get("sv") is None or r.get("status") not in ("settled", "finalized"): dropped += 1; continue
        sv = float(r["sv"]); yp = float(x["yes_price_dollars"])
        side = x["taker_side"]
        p = yp if side == "yes" else 1 - yp            # price taker paid for their side
        pay = sv if side == "yes" else 1 - sv
        rows.append({"day": w["start"], "half": "D" if w["start"] < SPLIT else "C", "p": p, "pay": pay, "side": side,
                     "legs": r.get("legs"), "count": float(x["count_fp"])})
print("trades used", len(rows), "dropped (unsettled/missing)", dropped)
def report(name, rs, rate):
    if len(rs) < 2: print(name, "n", len(rs)); return None
    pn = [r["p"] - r["pay"] - rate * r["p"] * (1 - r["p"]) for r in rs]
    risk = sum(1 - r["p"] for r in rs)   # maker's capital at risk per contract = payout - price on the other side
    n = len(pn); mu = sum(pn) / n
    byd = defaultdict(list)
    for r, x in zip(rs, pn): byd[r["day"]].append(x)
    dm = [sum(v) / len(v) for v in byd.values()]; dmu = sum(dm) / len(dm)
    dsd = math.sqrt(sum((x - dmu) ** 2 for x in dm) / (len(dm) - 1)) if len(dm) > 1 else 0
    dt_ = dmu / (dsd / math.sqrt(len(dm))) if dsd else 0
    print(f"{name:42s} n={n:5d} days={len(dm):2d} maker mean={mu:+.4f}/contract day_t={dt_:+.2f} return on risk={sum(pn) / risk:+.3f}  (mean taker price {sum(r['p'] for r in rs) / n:.3f}, hit {sum(r['pay'] for r in rs) / n:.3f})")
    return n, mu, dt_, sum(pn) / risk
for rate, lab in ((0.035, "maker fee 0.035 (PRIMARY)"), (0.0175, "maker fee 0.0175"), (0.0, "no maker fee")):
    print(f"\n== {lab}")
    D = report("discovery Aug20-Sep10", [r for r in rows if r["half"] == "D"], rate)
    C = report("confirmation Sep11-Oct2", [r for r in rows if r["half"] == "C"], rate)
    if rate == 0.035 and C and D:
        ok = C[0] >= 1000 and C[1] > 0 and C[2] >= 2 and C[3] >= 0.01 and D[1] > 0
        print("VERDICT:", "PASS" if ok else "FAIL")
print("\n-- by taker side / legs (all, fee 0.035) --")
for s in ("yes", "no"): report(f"taker bought {s}", [r for r in rows if r["side"] == s], 0.035)
for lo, hi in ((2, 2), (3, 4), (5, 7), (8, 99)):
    report(f"legs {lo}-{hi}", [r for r in rows if r["legs"] and lo <= r["legs"] <= hi], 0.035)
