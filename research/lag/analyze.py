"""docs/LAG_PREREG_2026-10-05.md"""
import json, math, os, bisect, datetime as dt
from collections import defaultdict
games = json.load(open("games.json"))
SPLIT = int(dt.datetime(2026, 9, 16, tzinfo=dt.timezone.utc).timestamp())
fee = lambda p: 0.07 * p * (1 - p)
agree = disagree = 0
xc = defaultdict(float); xn = defaultdict(int)
events = defaultdict(list)    # d -> [(day, pnl, cost, split)]
rev = []
for gm in games:
    fp, fk = f"pmt/{gm['pm']}.json", f"kt/{gm['k']}.json"
    if not (os.path.exists(fp) and os.path.exists(fk)) or gm["k_sv"] is None: continue
    ksv = float(gm["k_sv"])
    if (ksv == 1.0) == (gm["payout"][0] == 1.0): agree += 1
    else: disagree += 1; continue
    pmt = sorted([(t, p if o == 0 else 1 - p) for t, o, p, s, side in json.load(open(fp)) if t >= gm["start"]])
    kt = sorted([(t, yp, n, tk) for t, yp, n, tk in json.load(open(fk)) if t >= gm["start"]])
    if len(pmt) < 50 or len(kt) < 50: continue
    t0, t1 = gm["start"], min(pmt[-1][0], kt[-1][0])
    if t1 - t0 < 1800: continue
    # 1-second grids
    def grid(rows):
        out, j, last = [], 0, None
        for s in range(t0, t1 + 1):
            while j < len(rows) and rows[j][0] <= s: last = rows[j][1]; j += 1
            out.append(last)
        return out
    gp, gk = grid(pmt), grid([(t, yp) for t, yp, n, tk in kt])
    W = 10
    dp = [(gp[i] - gp[i - W]) if gp[i] is not None and gp[i - W] is not None else None for i in range(W, len(gp))]
    dk = [(gk[i] - gk[i - W]) if gk[i] is not None and gk[i - W] is not None else None for i in range(W, len(gk))]
    for lag in range(-60, 61):
        s = 0.0; n = 0
        for i in range(max(0, -lag), min(len(dp), len(dk) - lag)):
            a, b = dp[i], dk[i + lag]
            if a is not None and b is not None: s += a * b; n += 1
        xc[lag] += s; xn[lag] += n
    # events: Polymarket jump >= 0.05 within 15 s
    kts = [r[0] for r in kt]; last_ev = -1e9
    for i, (t, p) in enumerate(pmt):
        if t - last_ev < 300: continue
        j = bisect.bisect_left([r[0] for r in pmt], t - 15)
        window = [q for _, q in pmt[j:i]]
        if not window: continue
        move = p - window[0]
        if abs(move) < 0.05: continue
        direction = 1 if move > 0 else -1
        ki = bisect.bisect_right(kts, t) - 1; k0 = bisect.bisect_right(kts, t - 15) - 1
        if ki < 0 or k0 < 0: continue
        kmove = (kt[ki][1] - kt[k0][1]) * direction
        if kmove >= abs(move) / 2: continue
        last_ev = t
        for d in (2, 5, 10):
            # first Kalshi trade at/after t+d where taker bought our side; YES side if direction>0 (team0 up)
            want = "yes" if direction > 0 else "no"
            k = bisect.bisect_left(kts, t + d)
            while k < len(kt) and kt[k][3] != want: k += 1
            if k >= len(kt) or kt[k][0] > t + 120: continue
            price = kt[k][1] if want == "yes" else 1 - kt[k][1]
            pay = ksv if want == "yes" else 1 - ksv
            events[d].append((t // 86400, pay - price - fee(price), price + fee(price), t >= SPLIT, kt[k][0] - t))
print(f"games with both venues: agree on result {agree}, disagree {disagree}")
peak = max(xc, key=lambda l: xc[l] / max(1, xn[l]))
print("cross-correlation of 10s changes (positive lag = Kalshi follows Polymarket):")
print("  ", " ".join(f"{l:+d}:{xc[l] / max(1, xn[l]) * 1e4:.2f}" for l in (-30, -15, -10, -5, -2, 0, 2, 5, 10, 15, 30)), " peak lag", peak, "s")
def st(rows):
    n = len(rows)
    if n < 2: return n, 0, 0, 0
    xs = [r[1] for r in rows]; mu = sum(xs) / n
    byd = defaultdict(list)
    for r in rows: byd[r[0]].append(r[1])
    dm = [sum(v) / len(v) for v in byd.values()]; dmu = sum(dm) / len(dm)
    dsd = math.sqrt(sum((x - dmu) ** 2 for x in dm) / (len(dm) - 1)) if len(dm) > 1 else 0
    return n, mu, (dmu / (dsd / math.sqrt(len(dm))) if dsd else 0), sum(xs) / sum(r[2] for r in rows)
for d in (2, 5, 10):
    D = st([r for r in events[d] if not r[3]]); C = st([r for r in events[d] if r[3]])
    lat = sorted(r[4] for r in events[d]); med = lat[len(lat) // 2] if lat else None
    ok = d == 5 and C[0] >= 100 and C[1] > 0 and C[2] >= 2 and C[3] >= 0.01 and D[1] > 0
    print(f"d={d:>2d}s discovery n={D[0]} mean={D[1]:+.4f} day_t={D[2]:+.2f} roi={D[3]:+.3f} | confirmation n={C[0]} mean={C[1]:+.4f} day_t={C[2]:+.2f} roi={C[3]:+.3f} | median fill delay {med}s" + ("  -> " + ("PASS" if ok else "FAIL") if d == 5 else ""))
