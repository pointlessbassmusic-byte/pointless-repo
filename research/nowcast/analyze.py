"""Pre-registered nowcast test (docs/NOWCAST_PREREG_2026-10-02.md)."""
import json, glob, math, csv, datetime as dt, re, sys
from collections import defaultdict, Counter
UTC = dt.timezone.utc
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
STD = {"NYC": -5, "MIA": -5, "PHL": -5, "BOS": -5, "DCA": -5, "ATL": -5, "EWR": -5, "TTN": -5,
       "MDW": -6, "AUS": -6, "DFW": -6, "MSY": -6, "OKC": -6, "SAT": -6, "HOU": -6, "MSP": -6, "SDF": -5,
       "DEN": -7, "PHX": -7, "LAX": -8, "SEA": -8, "SFO": -8, "LAS": -8, "SAN": -8}
CHECK = (11, 13, 15, 17)
MARGINS = (0.03, 0.05, 0.10)
MON = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
FIT_END = dt.date(2026, 7, 31)

def evdate(ev):
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})$", ev)
    return dt.date(2000 + int(m.group(1)), MON[m.group(2)], int(m.group(3))) if m else None

def yes_if(m, H):
    st, f, c = m["strike_type"], m.get("floor_strike"), m.get("cap_strike")
    if st == "greater": return H > f
    if st == "less": return H < c
    if st == "between": return f <= H <= c
    return None

def load_obs(code):
    out = []
    try:
        for r in csv.DictReader(open(f"obs/{code}.csv")):
            if r["tmpf"] in ("M", ""): continue
            out.append((int(dt.datetime.strptime(r["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp()), float(r["tmpf"])))
    except FileNotFoundError: pass
    return sorted(out)

def load_cli(code):
    try: d = json.load(open(f"cli/{code}.json"))
    except FileNotFoundError: return {}
    return {r["valid"]: r["high"] for r in d.get("results", []) if isinstance(r.get("high"), (int, float))}

rows = []   # (station, date, checkpoint, D or None, brackets[(m, bid, ask, sv)], mult, source)
stats = Counter()
for fn in sorted(glob.glob("mk/*.json")):
    S = json.load(open(fn)); code = S["code"]
    if not code or code not in STD or not S["markets"]: continue
    try: cd = json.load(open(f"cd/{S['series']}.json"))
    except FileNotFoundError: continue
    obs = load_obs(code); cli = load_cli(code); ots = [o[0] for o in obs]
    off = STD[code]; mult = float(S.get("mult") or 1)
    evs = defaultdict(list)
    for m in S["markets"]: evs[m["event_ticker"]].append(m)
    import bisect
    for ev, ms in evs.items():
        d = evdate(ev)
        if not d: continue
        src = "twc" if any("Weather Company" in (m.get("rules_primary") or "") for m in ms) else "nws"
        H = cli.get(d.isoformat())
        if H is not None:
            ok = [yes_if(m, H) == (m["result"] == "yes") for m in ms if m["result"] in ("yes", "no")]
            stats["cli_match" if all(ok) else "cli_mismatch"] += 1
            if not all(ok): H = None
        start = int(dt.datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp()) - off * 3600
        for h in CHECK:
            t = start + h * 3600
            i0 = bisect.bisect_left(ots, start); i1 = bisect.bisect_right(ots, t - 600)
            if i1 <= i0: stats["no_obs"] += 1; continue
            M = round(max(o[1] for o in obs[i0:i1]))
            br = []
            for m in ms:
                if P(m["close_time"]) <= t or m.get("settlement_value_dollars") is None: continue
                q = [c for c in cd.get(m["ticker"], []) if c[0] == t]
                if not q or q[0][1] is None or q[0][2] is None: continue
                b, a = float(q[0][1]), float(q[0][2])
                if not (0 < b < a < 1): continue
                br.append((m, b, a, float(m["settlement_value_dollars"])))
            rows.append((code, d, h, (H - M) if H is not None else None, M, br, mult, src))
print("rows", len(rows), dict(stats))

# fit
fit = defaultdict(Counter)
for code, d, h, D, M, br, mult, src in rows:
    if d <= FIT_END and D is not None: fit[(code, h)][D] += 1
def dist(code, h):
    c = fit.get((code, h))
    if not c or sum(c.values()) < 10: return None
    lo, hi = min(c) - 3, max(c) + 3
    w = {k: c.get(k, 0) + 1 for k in range(lo, hi + 1)}
    s = sum(w.values()); return {k: v / s for k, v in w.items()}

def prob(m, M, pd):
    return sum(p for k, p in pd.items() if yes_if(m, M + k))

def run(sel, margin):
    obs = []
    for code, d, h, D, M, br, mult, src in rows:
        if not sel(d, h, code, src): continue
        pd = dist(code, h)
        if not pd or not br: continue
        pnl = cost = 0.0; n = 0
        for m, b, a, sv in br:
            p = prob(m, M, pd)
            fy = 0.07 * mult * a * (1 - a); pn = 1 - b; fn_ = 0.07 * mult * pn * (1 - pn)
            if p - a - fy >= margin: pnl += sv - a - fy; cost += a + fy; n += 1
            elif (1 - p) - pn - fn_ >= margin: pnl += (1 - sv) - pn - fn_; cost += pn + fn_; n += 1
        if n: obs.append((d, pnl, cost))
    return obs

def summ(name, obs):
    n = len(obs)
    if n < 2: print(f"{name}: n={n}"); return
    xs = [o[1] for o in obs]; mu = sum(xs) / n; sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))
    byd = defaultdict(list)
    for d, p, c in obs: byd[d].append(p)
    dm = [sum(v) / len(v) for v in byd.values()]; nd = len(dm); dmu = sum(dm) / nd
    dsd = math.sqrt(sum((x - dmu) ** 2 for x in dm) / (nd - 1)) if nd > 1 else 0
    roi = sum(o[1] for o in obs) / sum(o[2] for o in obs)
    print(f"{name}: n={n} days={nd} mean={mu:+.4f} t={mu/(sd/math.sqrt(n)) if sd else 0:.2f} day_t={dmu/(dsd/math.sqrt(nd)) if dsd else 0:.2f} roi={roi:+.3f}")

test = lambda d, h, c, s: d > FIT_END
print("\n== PRIMARY (test half, m=0.05, pooled) ==")
summ("primary", run(test, 0.05))
print("\n-- secondary --")
for mg in MARGINS: summ(f"test m={mg}", run(test, mg))
for h in CHECK: summ(f"test m=0.05 checkpoint {h}:00 LST", run(lambda d, hh, c, s, h=h: d > FIT_END and hh == h, 0.05))
for src in ("nws", "twc"): summ(f"test m=0.05 source={src}", run(lambda d, h, c, s, src=src: d > FIT_END and s == src, 0.05))
summ("fit half (in-sample, biased) m=0.05", run(lambda d, h, c, s: d <= FIT_END, 0.05))
print("\nper station (test, m=0.05):")
for code in sorted(STD): 
    o = run(lambda d, h, c, s, code=code: d > FIT_END and c == code, 0.05)
    if o: summ("  " + code, o)
