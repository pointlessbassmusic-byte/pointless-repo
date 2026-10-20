"""Pre-registered grid (docs/SURVEY_PREREG_2026-10-01.md). Builds per-event cell
observations, then applies discovery/confirmation bars by category and by series."""
import json, math, bisect, datetime as dt, sys
from collections import defaultdict
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
NOW = 1790812800
FRACS = (0.50, 0.75, 0.90, 0.98)
BUCKETS = (0.03, 0.15, 0.35, 0.65, 0.85, 0.97)
meta = {s["ticker"]: s for s in json.load(open("series_all.json"))}

def bucket(m):
    for i in range(5):
        if BUCKETS[i] <= m < BUCKETS[i + 1] or (i == 4 and m == 0.97):
            return i
    return None

def quote_at(cs, ts, t):
    i = bisect.bisect_right(ts, t) - 1
    while i >= 0:
        b, a = cs[i][1], cs[i][2]
        if b is not None and a is not None:
            return float(b), float(a)
        i -= 1
    return None

def mom(d):
    return "up" if d > 0.02 else "down" if d < -0.02 else "flat"

# obs[(group_kind, group, cell)][event] -> list of pnl, cost ; plus day per event
obs = defaultdict(lambda: defaultdict(list))
evday, evhalf = {}, {}
nmk = nentry = 0
SEL = set(json.load(open("selected.json")))
for line in open("candles.jsonl"):
    r = json.loads(line)
    s = r["series"]; sm = meta.get(s, {})
    cat = sm.get("category", "?")
    mult = float(sm.get("fee_multiplier") or 1)
    maker_fees = sm.get("fee_type", "").startswith("quadratic_with")
    ev = r["event"]
    if ev not in SEL:
        continue
    for m in r["markets"]:
        nmk += 1
        cs = [c for c in m["candles"]]
        if not cs or m.get("settlement_value_dollars") is None:
            continue
        sv = float(m["settlement_value_dollars"])
        o = P(m["open_time"]); close = P(m["close_time"])
        te = m.get("expected_expiration_time")
        if te:
            T = P(te)
        elif not m.get("can_close_early"):
            T = close
        else:
            continue
        if T <= o:
            continue
        off = (NOW - close) // 86400 + 1
        off = 2 + 6 * round((off - 2) / 6)
        evday[ev] = off
        evhalf[ev] = "disc" if off >= 62 else "conf"
        ts = [c[0] for c in cs]
        for f in FRACS:
            t = o + f * (T - o)
            if t >= close:
                continue
            q = quote_at(cs, ts, t)
            if not q:
                continue
            b, a = q
            if not (0 < b < a < 1) or a - b > 0.10:
                continue
            mid = (a + b) / 2
            bk = bucket(mid)
            if bk is None:
                continue
            q0 = quote_at(cs, ts, o + (f - 0.25) * (T - o))
            mo = mom(mid - (q0[0] + q0[1]) / 2) if q0 and 0 < q0[0] < q0[1] < 1 else None
            nentry += 1
            for ex in ("taker", "maker_ub"):
                for side in ("YES", "NO"):
                    if ex == "taker":
                        p = a if side == "YES" else 1 - b
                        fee = 0.07 * mult * p * (1 - p)
                    else:
                        p = b if side == "YES" else 1 - a
                        fee = 0.0175 * mult * p * (1 - p) if maker_fees else 0.0
                    pay = sv if side == "YES" else 1 - sv
                    pnl = pay - p - fee
                    for mm in ("any", mo) if mo else ("any",):
                        cell = (ex, side, bk, f, mm)
                        for g in (("cat", cat), ("series", s)):
                            obs[(g[0], g[1], cell)][ev].append((pnl, p + fee))

def stats(evs, keys):
    xs = [sum(p for p, _ in evs[e]) / len(evs[e]) for e in keys]
    cost = sum(sum(c for _, c in evs[e]) / len(evs[e]) for e in keys)
    n = len(xs)
    if n < 2:
        return n, 0, 0, 0, 0, 0
    mu = sum(xs) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))
    t = mu / (sd / math.sqrt(n)) if sd > 0 else 0
    byd = defaultdict(list)
    for e, x in zip(keys, xs):
        byd[evday[e]].append(x)
    dm = [sum(v) / len(v) for v in byd.values()]
    nd = len(dm)
    if nd >= 2:
        dmu = sum(dm) / nd
        dsd = math.sqrt(sum((x - dmu) ** 2 for x in dm) / (nd - 1))
        dt_ = dmu / (dsd / math.sqrt(nd)) if dsd > 0 else 0
    else:
        dt_ = 0
    return n, mu, t, dt_, nd, (sum(xs) / cost if cost else 0)

rows = []
for (gk, g, cell), evs in obs.items():
    d = [e for e in evs if evhalf[e] == "disc"]
    c = [e for e in evs if evhalf[e] == "conf"]
    sd_, sc = stats(evs, d), stats(evs, c)
    rows.append((gk, g, cell, sd_, sc))
json.dump([[gk, g, list(cell), list(a), list(b)] for gk, g, cell, a, b in rows], open("grid_rows.json", "w"))
print("markets", nmk, "entries", nentry, "cells", len(rows))
for gk in ("cat", "series"):
    R = [r for r in rows if r[0] == gk]
    tested = [r for r in R if r[3][0] >= 30]
    disc = [r for r in tested if r[3][1] > 0 and r[3][2] >= 3]
    conf = [r for r in disc if r[4][0] >= 30 and r[4][1] > 0 and r[4][2] >= 2 and r[4][3] >= 2 and r[4][4] >= 5 and r[4][5] >= 0.01]
    print(f"\n== {gk}: cells with n_disc>=30: {len(tested)}; discovery passes: {len(disc)} "
          f"(null expectation at t>=3 one-sided ~{0.00135*len(tested):.1f}); confirmed: {len(conf)}")
    for r in sorted(disc, key=lambda r: -r[4][2])[:60]:
        tag = "CONFIRMED" if r in conf else ""
        print(r[1], r[2], "disc n=%d mu=%.4f t=%.2f dayt=%.2f nd=%d roi=%.3f" % r[3],
              "| conf n=%d mu=%.4f t=%.2f dayt=%.2f nd=%d roi=%.3f" % r[4], tag)
