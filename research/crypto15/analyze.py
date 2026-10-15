"""docs/CRYPTO15_PREREG_2026-10-05.md"""
import json, math, os, bisect, datetime as dt
from collections import defaultdict
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
SPLIT = P("2026-09-13T00:00:00Z")
SER = {"KXBTC15M": "BTC-USD", "KXETH15M": "ETH-USD", "KXSOL15M": "SOL-USD"}
fee = lambda p: 0.07 * p * (1 - p)
Phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
spot = {}
for s, prod in SER.items():
    d = {int(k): v for k, v in json.load(open(f"spot_{prod}.json")).items()}
    ks = sorted(d); spot[s] = (ks, d)
def bars(s, t, n):
    """last n complete 1-min bars ending at t: list of (low, high, close)."""
    ks, d = spot[s]; i = bisect.bisect_right(ks, t - 60)   # bar starting at t-60 closes at t
    sel = ks[max(0, i - n):i]
    return [d[k] for k in sel] if sel and sel[-1] >= t - 180 else []
def dmi_adx(b, n=14):
    if len(b) < 2 * n + 1: return None
    tr, pdm, mdm = [], [], []
    for (l0, h0, c0), (l1, h1, c1) in zip(b, b[1:]):
        up, dn = h1 - h0, l0 - l1
        pdm.append(up if up > dn and up > 0 else 0.0); mdm.append(dn if dn > up and dn > 0 else 0.0)
        tr.append(max(h1 - l1, abs(h1 - c0), abs(l1 - c0)))
    def wilder(x):
        s = sum(x[:n]); out = [s]
        for v in x[n:]: s = s - s / n + v; out.append(s)
        return out
    atr, ap, am = wilder(tr), wilder(pdm), wilder(mdm)
    pdi = [100 * p / a if a else 0 for p, a in zip(ap, atr)]; mdi = [100 * m / a if a else 0 for m, a in zip(am, atr)]
    dx = [100 * abs(p - m) / (p + m) if p + m else 0 for p, m in zip(pdi, mdi)]
    if len(dx) < n: return None
    adx = sum(dx[:n]) / n
    for v in dx[n:]: adx = (adx * (n - 1) + v) / n
    return pdi[-1], mdi[-1], adx
ms = json.load(open("markets.json"))
cd = {}
for f in os.listdir("cd"): cd.update(json.load(open("cd/" + f)))
def quote(cs, t):
    best = None
    for c in cs:
        if c[0] <= t and c[1] is not None and c[2] is not None: best = c
        elif c[0] > t: break
    if not best or best[0] < t - 120: return None
    b, a = float(best[1]), float(best[2])
    return (b, a) if 0 < b < a < 1 else None
res = defaultdict(list)   # cell -> [(close_ts, pnl, cost)]
for m in ms:
    s = m["ticker"].split("-")[0]; o, c = P(m["open_time"]), P(m["close_time"])
    sv = m.get("settlement_value_dollars"); cs = sorted(cd.get(m["ticker"], []))
    if sv is None or not cs or m.get("floor_strike") is None: continue
    sv = float(sv); K = float(m["floor_strike"])
    def take(cell, side, q):
        b, a = q
        p = a if side == "YES" else 1 - b
        pay = sv if side == "YES" else 1 - sv
        res[cell].append((c, pay - p - fee(p), p + fee(p)))
    # S1
    for k in (5, 10):
        t = o + 60 * k; q = quote(cs, t); r = dmi_adx(bars(s, t, 60))
        if q and r and r[2] >= 25 and r[0] != r[1]:
            side = "YES" if r[0] > r[1] else "NO"
            take(f"S1_k{k}", side, q); take(f"S1_k{k}_inverse", "NO" if side == "YES" else "YES", q)
    # S2
    for lag, cell in ((60, "S2_lag1"), (0, "S2_same")):
        for k in range(1, 14):
            t = o + 60 * k; b = bars(s, t, 61)
            if len(b) < 61: continue
            rets = [math.log(b[i + 1][2] / b[i][2]) for i in range(60)]
            mu = sum(rets) / 60; sig = math.sqrt(sum((x - mu) ** 2 for x in rets) / 59)
            tau = (c - t) / 60
            if sig <= 0 or tau <= 0: continue
            fair = Phi(math.log(b[-1][2] / K) / (sig * math.sqrt(tau)))
            q0 = quote(cs, t)
            if not q0: continue
            b0, a0 = q0
            side = "YES" if fair - a0 - fee(a0) >= 0.03 else "NO" if (1 - fair) - (1 - b0) - fee(1 - b0) >= 0.03 else None
            if side:
                q = quote(cs, t + lag)
                if q: take(cell, side, q)
                break
# S3
for fn in os.listdir("tr"):
    t_ = fn[:-5]; m = next(x for x in ms if x["ticker"] == t_)
    if m.get("settlement_value_dollars") is None: continue
    sv = float(m["settlement_value_dollars"]); cs = sorted(cd.get(t_, [])); tr = sorted(json.load(open("tr/" + fn))); tts = [x[0] for x in tr]
    o, c = P(m["open_time"]), P(m["close_time"]); pt = po = cost = 0.0; q100 = 100.0
    for i, (ts, b, a) in enumerate(cs):
        if b is None or a is None: continue
        b, a = float(b), float(a)
        if not (0 < b < a < 1) or a - b > 0.10: continue
        win = tr[bisect.bisect_right(tts, ts):bisect.bisect_right(tts, min(ts + 60, c))]
        for side, px in (("yes", b), ("no", 1 - a)):
            thr = tch = False
            for _, yp, n, tk in win:
                if side == "yes" and tk == "no": thr |= yp < px - 1e-9; tch |= yp <= px + 1e-9
                if side == "no" and tk == "yes": thr |= (1 - yp) < px - 1e-9; tch |= (1 - yp) <= px + 1e-9
            pay = sv if side == "yes" else 1 - sv
            if thr: pt += q100 * (pay - px); cost += q100 * px
            if tch: po += q100 * (pay - px)
    res["S3_through"].append((c, pt, cost)); res["S3_touch"].append((c, po, cost or 1))
def stats(rows):
    n = len(rows)
    if n < 2: return n, 0, 0, 0, 0
    xs = [r[1] for r in rows]; mu = sum(xs) / n
    byd = defaultdict(list)
    for r in rows: byd[r[0] // 86400].append(r[1])
    dm = [sum(v) / len(v) for v in byd.values()]; dmu = sum(dm) / len(dm)
    dsd = math.sqrt(sum((x - dmu) ** 2 for x in dm) / (len(dm) - 1)) if len(dm) > 1 else 0
    return n, mu, (dmu / (dsd / math.sqrt(len(dm))) if dsd else 0), sum(xs) / max(1e-9, sum(r[2] for r in rows)), len(dm)
print(f"{'cell':18s} | discovery n mean dayt roi | confirmation n mean dayt roi days | verdict")
for cell in sorted(res):
    d = stats([r for r in res[cell] if r[0] < SPLIT]); f = stats([r for r in res[cell] if r[0] >= SPLIT])
    primary = cell in ("S1_k5", "S1_k10", "S2_lag1", "S3_through")
    ok = f[0] >= 300 and f[1] > 0 and f[2] >= 2 and f[3] >= 0.01 and d[1] > 0
    unit = "$" if cell.startswith("S3") else ""
    print(f"{cell:18s} | {d[0]:5d} {d[1]:+.4f} {d[2]:+.2f} {d[3]:+.3f} | {f[0]:5d} {f[1]:+.4f} {f[2]:+.2f} {f[3]:+.3f} {f[4]:3d} | {('PASS' if ok else 'fail') if primary else '(secondary)'}")
