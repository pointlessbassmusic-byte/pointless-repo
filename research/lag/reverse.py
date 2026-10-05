"""Descriptive (pre-registered as such): Kalshi jump >= 0.05 in 15 s that Polymarket has not
half-followed -> buy on Polymarket at the first same-direction Polymarket BUY >= t+d."""
import json, math, os, bisect
from collections import defaultdict
games = json.load(open("games.json"))
pfee = lambda p: 0.05 * p * (1 - p)
res = defaultdict(list)
for gm in games:
    fp, fk = f"pmt/{gm['pm']}.json", f"kt/{gm['k']}.json"
    if not (os.path.exists(fp) and os.path.exists(fk)): continue
    pay0 = gm["payout"][0]
    pm = sorted([(t, p if o == 0 else 1 - p, o, side) for t, o, p, s, side in json.load(open(fp)) if t >= gm["start"]])
    kt = sorted([(t, yp) for t, yp, n, tk in json.load(open(fk)) if t >= gm["start"]])
    if len(pm) < 50 or len(kt) < 50: continue
    pts = [r[0] for r in pm]; kts = [r[0] for r in kt]; last = -1e9
    for i, (t, p) in enumerate(kt):
        if t - last < 300: continue
        j = bisect.bisect_left(kts, t - 15)
        if j >= i: continue
        move = p - kt[j][1]
        if abs(move) < 0.05: continue
        dirn = 1 if move > 0 else -1
        a, b = bisect.bisect_right(pts, t) - 1, bisect.bisect_right(pts, t - 15) - 1
        if a < 0 or b < 0 or (pm[a][1] - pm[b][1]) * dirn >= abs(move) / 2: continue
        last = t
        want = 0 if dirn > 0 else 1
        for d in (2, 5, 10, 30):
            k = bisect.bisect_left(pts, t + d)
            while k < len(pm) and not (pm[k][2] == want and pm[k][3] == "BUY"): k += 1
            if k >= len(pm) or pm[k][0] > t + 120: continue
            price = pm[k][1] if want == 0 else 1 - pm[k][1]
            pay = pay0 if want == 0 else 1 - pay0
            res[d].append(pay - price - pfee(price))
for d, v in sorted(res.items()):
    mu = sum(v) / len(v); sd = math.sqrt(sum((x - mu) ** 2 for x in v) / (len(v) - 1))
    print(f"Kalshi jump -> buy Polymarket at +{d}s: n={len(v)} mean={mu:+.4f}/share t={mu / (sd / math.sqrt(len(v))):+.2f}")
