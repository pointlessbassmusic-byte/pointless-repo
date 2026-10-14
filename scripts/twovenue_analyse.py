"""Analysis for docs/PMUS_KALSHI_PREREG_2026-10-14.md (A arb, B lead-lag,
C maker-on-PM-US/hedge-on-Kalshi). Reads the sampler's sqlite."""
import math
import random
import sqlite3
import sys
from collections import defaultdict

DB = sys.argv[1]
TICK = 0.01


def fee_k(p, game):
    m = 0.5 if game.startswith("KXMLBGAME") else 1.0
    return 0.07 * m * p * (1 - p)


def fee_p(p):
    return 0.0695 * p * (1 - p)


conn = sqlite3.connect(DB)
rows = conn.execute("SELECT ts, sport, game, start, kbid, kask, kbsz, kasz, pbid, pask, pbsz, pasz"
                    " FROM q ORDER BY game, ts").fetchall()
games = defaultdict(list)
for r in rows:
    games[r[2]].append(r)
two_sided = [r for r in rows if None not in (r[4], r[5], r[8], r[9])]
print(f"samples {len(rows)}, two-sided on both {len(two_sided)}, games {len(games)}, "
      f"span {(max(r[0] for r in rows) - min(r[0] for r in rows)) / 60:.0f} min")


def phase(r):
    return "in-play" if r[3] is not None and r[0] >= r[3] else "pre-game"


def boot_ci(vals_by_game, n=2000, seed=7):
    keys = [k for k, v in vals_by_game.items() if v]
    if len(keys) < 2:
        return None
    rng = random.Random(seed)
    allv = [x for k in keys for x in vals_by_game[k]]
    est = sum(allv) / len(allv)
    means = []
    for _ in range(n):
        s = [x for _ in keys for x in vals_by_game[rng.choice(keys)]]
        means.append(sum(s) / len(s))
    means.sort()
    return est, means[int(0.025 * n)], means[int(0.975 * n) - 1]


# ---------------------------------------------------------------- A
print("\n== A: complement arbitrage (taker both legs), net per pair")
for ph in ("pre-game", "in-play", "all"):
    sel = [r for r in two_sided if ph == "all" or phase(r) == ph]
    hits, games_hit, best = 0, set(), -9.0
    nets = []
    for ts, sport, g, st, kb, ka, kbs, kas, pb, pa, pbs, pas in sel:
        n1 = pb - ka - fee_k(ka, g) - fee_p(1 - pb)          # X on Kalshi + NOT-X on PM US
        n2 = kb - pa - fee_p(pa) - fee_k(1 - kb, g)          # X on PM US + NOT-X on Kalshi
        s1 = min(kas or 0, pbs or 0)
        s2 = min(pas or 0, kbs or 0)
        for net, size in ((n1, s1), (n2, s2)):
            nets.append(net)
            best = max(best, net)
            if net >= 0.01 and size >= 10:
                hits += 1
                games_hit.add(g)
    if not sel:
        continue
    share = hits / len(sel)
    nets.sort()
    print(f"{ph:9s} samples {len(sel):5d}  >=1c with size: {hits} ({share:.2%}) on {len(games_hit)} games;"
          f"  best {best * 100:+.2f}c  median {nets[len(nets) // 2] * 100:+.2f}c")
    if ph == "all":
        verdict_a = "PASS" if share >= 0.01 and len(games_hit) >= 5 else "FAIL"
print("A verdict:", verdict_a)


# ---------------------------------------------------------------- B
def ok(r):
    return None not in (r[4], r[5], r[8], r[9]) and r[5] - r[4] <= 0.05 and r[9] - r[8] <= 0.05


def mid(r, v):
    return (r[4] + r[5]) / 2 if v == "K" else (r[8] + r[9]) / 2


def ols_cluster(xs, ys, cl):
    n = len(xs)
    if n < 10:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    by = defaultdict(float)
    for x, y, c in zip(xs, ys, cl):
        by[c] += (x - mx) * (y - a - b * x)
    var = sum(v * v for v in by.values()) / sxx ** 2
    se = math.sqrt(var) if var > 0 else float("nan")
    return b, se, b / se if se and se == se else float("nan"), n, len(by)


print("\n== B: lead-lag. dm_venue(t+h) = a + beta * (mK - mP)(t); SE clustered by game")
verdict_b = {}
for ph in ("pre-game", "in-play", "all"):
    for h in (20, 60, 180):
        res = {}
        for v in ("K", "P"):
            xs, ys, cl = [], [], []
            for g, rs in games.items():
                for i, r in enumerate(rs):
                    if not ok(r) or (ph != "all" and phase(r) != ph):
                        continue
                    target = r[0] + h
                    j = min(range(i + 1, len(rs)), key=lambda j: abs(rs[j][0] - target), default=None)
                    if j is None or abs(rs[j][0] - target) > 12 or not ok(rs[j]):
                        continue
                    xs.append(mid(r, "K") - mid(r, "P"))
                    ys.append(mid(rs[j], v) - mid(r, v))
                    cl.append(g)
            res[v] = ols_cluster(xs, ys, cl)
        line = f"{ph:9s} h={h:3d}s"
        for v, name in (("P", "PM US follows (beta_P>0)"), ("K", "Kalshi follows (beta_K<0)")):
            o = res[v]
            line += (f" | {name}: " + ("n/a" if o is None else
                                       f"beta {o[0]:+.3f} se {o[1]:.3f} t {o[2]:+.1f} n {o[3]} games {o[4]}"))
        print(line)
        if ph == "all" and h == 60:
            verdict_b = res
bp, bk = verdict_b.get("P"), verdict_b.get("K")
print("B verdict:", "PM US follows Kalshi" if bp and bp[0] > 0 and bp[2] >= 3 else "PM US does not follow",
      "|", "Kalshi follows PM US" if bk and bk[0] < 0 and bk[2] <= -3 else "Kalshi does not follow")

# maker leg on the lagging venue (descriptive): rest one tick inside PM US on the side of the gap
marks = defaultdict(list)
for g, rs in games.items():
    for i, r in enumerate(rs):
        if not ok(r):
            continue
        gap = mid(r, "K") - mid(r, "P")
        if abs(gap) < TICK:
            continue
        target = r[0] + 180
        j = min(range(i + 1, len(rs)), key=lambda j: abs(rs[j][0] - target), default=None)
        if j is None or abs(rs[j][0] - target) > 12 or not ok(rs[j]):
            continue
        if gap > 0:
            price = min(r[8] + TICK, r[9] - TICK) if r[9] - r[8] > TICK + 1e-9 else r[8]
            marks[g].append(mid(rs[j], "P") - price)
        else:
            price = max(r[9] - TICK, r[8] + TICK) if r[9] - r[8] > TICK + 1e-9 else r[9]
            marks[g].append(price - mid(rs[j], "P"))
ci = boot_ci(marks)
nm = sum(len(v) for v in marks.values())
print(f"B maker leg on PM US, |gap|>=1c, marked at +180 s (no fill model): n {nm}"
      + ("" if ci is None else f", mean {ci[0] * 100:+.2f}c [{ci[1] * 100:+.2f}, {ci[2] * 100:+.2f}]"))

# ---------------------------------------------------------------- C
print("\n== C: rest on PM US (no fee), hedge as taker on Kalshi; locked per pair")
for ph in ("pre-game", "in-play", "all"):
    sel = [r for r in two_sided if ph == "all" or phase(r) == ph]
    if not sel:
        continue
    good = 0
    locks = []
    for ts, sport, g, st, kb, ka, kbs, kas, pb, pa, pbs, pas in sel:
        inside = pa - pb > TICK + 1e-9
        bx = pb + TICK if inside else pb                       # our bid for X on PM US
        l1 = kb - bx - fee_k(1 - kb, g)                       # hedge: buy NOT-X on Kalshi at 1-kb
        ask_x = pa - TICK if inside else pa                   # our bid for NOT-X = selling X at ask_x
        l2 = ask_x - ka - fee_k(ka, g)                        # hedge: buy X on Kalshi at ka
        best = max(l1, l2)
        locks.append(best)
        good += best >= 0.01
    locks.sort()
    print(f"{ph:9s} samples {len(sel):5d}: lock >= 1c available in {good} ({good / len(sel):.2%});"
          f" median best lock {locks[len(locks) // 2] * 100:+.2f}c, p95 {locks[int(0.95 * len(locks))] * 100:+.2f}c")
