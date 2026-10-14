"""Analysis for docs/KALSHI_TOUCH_PREREG_2026-10-14.md (Result 15 on Kalshi)."""
import bisect
import random
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, "/home/user/pointless-repo")
from sportsbot.exchanges.kalshi import KNOWN_FEE_MULTIPLIERS  # noqa: E402

DB = sys.argv[1]
TICK = 0.01
DEADLINES = (("120 s", 120.0), ("30 min", 1800.0), ("to cut", float("inf")))


def maker_fee(price, ticker):
    mult = KNOWN_FEE_MULTIPLIERS.get(ticker.split("-")[0], 1.0)
    return 0.0175 * mult * price * (1 - price)


conn = sqlite3.connect(DB)
meta = {r[0]: r[1:] for r in conn.execute("SELECT ticker, event, sport, cut, result, truncated, n FROM m")}
tape = defaultdict(list)
for tk, ts, yes, taker, size in conn.execute("SELECT ticker, ts, yes, taker, size FROM t ORDER BY ticker, ts"):
    if taker in ("yes", "no") and 0.0 < yes < 1.0:
        tape[tk].append((ts, yes, taker))


def fills_for(tk):
    """Each print -> a resting order one tick behind it on the taker's side;
    fills on a later opposite-taker print through its level before the cut."""
    ev, sport, cut, result, trunc, n = meta[tk]
    prints = [p for p in tape.get(tk, []) if p[0] < cut]
    if len(prints) < 2:
        return []
    close = prints[-1][1]                                   # last pre-cut print, YES terms
    times = [p[0] for p in prints]
    out = []
    for i, (t0, p, taker) in enumerate(prints):
        if taker == "yes":                                  # taker bought YES -> we bid YES at p - tick
            level = round(p - TICK, 2)
            if level <= 0:
                continue
            side = "yes"
        else:                                               # taker sold YES -> we offer YES at p + tick
            level = round(p + TICK, 2)
            if level >= 1:
                continue
            side = "no"                                     # i.e. we buy NO at 1 - level
        j0 = bisect.bisect_right(times, t0)
        hit = None
        for j in range(j0, len(prints)):
            t1, q, tk2 = prints[j]
            if side == "yes" and tk2 == "no" and q <= level + 1e-9:
                hit = t1
                break
            if side == "no" and tk2 == "yes" and q >= level - 1e-9:
                hit = t1
                break
        if hit is None:
            continue
        wait = hit - t0
        price = level if side == "yes" else 1 - level        # cost in the side's own terms
        clv = (close - level) if side == "yes" else (level - close)
        won = result if side == "yes" else 1 - result
        ret = (won - price - maker_fee(price, tk)) / price
        out.append({"game": ev, "wait": wait, "clv": clv, "ret": ret, "cut": cut})
    return out


def boot(rows, key, n=2000, seed=9):
    by = defaultdict(list)
    for r in rows:
        by[r["game"]].append(r[key])
    ks = list(by)
    if len(ks) < 2:
        return None
    allv = [x for k in ks for x in by[k]]
    est = sum(allv) / len(allv)
    rng = random.Random(seed)
    ms = []
    for _ in range(n):
        s = [x for _ in ks for x in by[rng.choice(ks)]]
        ms.append(sum(s) / len(s))
    ms.sort()
    return est, ms[int(.025 * n)], ms[int(.975 * n) - 1]


by_sport = defaultdict(list)
for tk in meta:
    by_sport[meta[tk][1]].append(tk)
print(f"markets {len(meta)}; truncated {sum(m[4] for m in meta.values())}; prints {sum(m[5] for m in meta.values())}")
for sport, tks in sorted(by_sport.items()):
    cuts = sorted({meta[t][2] for t in tks})
    split = cuts[int(0.7 * len(cuts))] if cuts else 0
    allfills = [f for tk in tks for f in fills_for(tk)]
    test = [f for f in allfills if f["cut"] >= split]
    games_test = len({meta[t][0] for t in tks if meta[t][2] >= split})
    print(f"\n== {sport}: {len(tks)} markets, {len({meta[t][0] for t in tks})} games; test games {games_test}")
    verdict = "INCONCLUSIVE"
    for label, dl in DEADLINES:
        sel = [f for f in test if f["wait"] <= dl]
        g = len({f["game"] for f in sel})
        c, r = boot(sel, "clv"), boot(sel, "ret")
        if c is None:
            print(f"  {label:7s} fills {len(sel)}: n/a")
            continue
        print(f"  {label:7s} fills {len(sel):6d} games {g:4d}  CLV/fill {c[0]*100:+.2f}c [{c[1]*100:+.2f}, {c[2]*100:+.2f}]"
              f"  return/$ {r[0]:+.3f} [{r[1]:+.3f}, {r[2]:+.3f}]")
        if label == "120 s":
            if g >= 30 and c[1] > 0 and r[0] > 0:
                verdict = "PASS"
            elif c[2] <= 0:
                verdict = "FAIL"
    print(f"  verdict ({sport}): {verdict}")
