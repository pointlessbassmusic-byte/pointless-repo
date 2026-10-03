"""Pre-registered historical LIP test (docs/LIP_HIST_PREREG_2026-10-03.md)."""
import json, math, sqlite3, bisect, sys, statistics, datetime as dt
from collections import defaultdict
sys.path.insert(0, "/home/user/pointless-repo")
from sportsbot.signals.lip import reference_price, order_score
Q = 100.0
UNIT = 1e-4
meta = {s["ticker"]: s for s in json.load(open("series_all.json"))}
SPLIT = int(dt.datetime(2026, 9, 17, tzinfo=dt.timezone.utc).timestamp())

# --- observed share from live snapshots (Oct 2-3), join best bid both sides
db = sqlite3.connect("lip.sqlite")
progs = defaultdict(list)
for t, s, e, tgt, dbps in db.execute("SELECT market_ticker, start_ts, end_ts, target_size, discount_bps FROM programs"):
    progs[t].append((s, e, tgt, dbps / 1e4))
cats = {t: c for t, c in db.execute("SELECT ticker, category FROM panel")}
shares = defaultdict(list)
for tk, ts, yj, nj in db.execute("SELECT ticker, ts, yes_bids, no_bids FROM snapshots"):
    p = next((x for x in progs[tk] if x[0] <= ts < x[1]), None)
    yes, no = [tuple(x) for x in json.loads(yj)], [tuple(x) for x in json.loads(nj)]
    if not p or not yes or not no: continue
    tot = ours = 0.0
    for book in (yes, no):
        px = max(b[0] for b in book); full = book + [(px, Q)]
        ref = reference_price(full, p[2])
        tot += sum(order_score(a, b, ref, p[3]) for a, b in full); ours += order_score(px, Q, ref, p[3])
    shares[cats.get(tk, "?")].append(ours / tot)
obs_share = {c: statistics.median(v) for c, v in shares.items() if len(v) >= 20}

# --- simulate fills per program
rows = []   # (cat, end, pool_usd, loss_through, loss_touch, fills_through, fills_touch, contracts_minutes)
skipped = defaultdict(int)
for line in open("data.jsonl"):
    r = json.loads(line)
    if r.get("sv") is None or "candles" not in r: skipped["unsettled"] += 1; continue
    sv = float(r["sv"]); s0 = meta.get(r["ticker"].split("-")[0], {})
    mult = float(s0.get("fee_multiplier") or 1); mfee = s0.get("fee_type", "").startswith("quadratic_with")
    cs = sorted((c for c in r["candles"] if c[1] is not None and c[2] is not None), key=lambda c: c[0])
    tr = sorted(r["trades"]); tts = [t[0] for t in tr]
    lt = lo = 0.0; ft = fo = 0; quoted = 0
    for i, (ts, b, a) in enumerate(cs):
        b, a = float(b), float(a)
        if not (0 < b < a < 1) or a - b > 0.10: continue
        nxt = cs[i + 1][0] if i + 1 < len(cs) else ts + 60
        nxt = min(nxt, ts + 60)
        win = tr[bisect.bisect_right(tts, ts):bisect.bisect_right(tts, nxt)]
        quoted += 1
        for side, px in (("yes", b), ("no", 1 - a)):
            thr = tch = False
            for t, yp, n, taker in win:
                if side == "yes" and taker == "no":
                    thr |= yp < px - 1e-9; tch |= yp <= px + 1e-9
                if side == "no" and taker == "yes":
                    thr |= (1 - yp) < px - 1e-9; tch |= (1 - yp) <= px + 1e-9
            fee = 0.0175 * mult * px * (1 - px) if mfee else 0.0
            pnl = Q * ((sv if side == "yes" else 1 - sv) - px - fee)
            if thr: lt -= pnl; ft += 1
            if tch: lo -= pnl; fo += 1
    if quoted == 0: skipped["never_quotable"] += 1; continue
    rows.append((r["cat"], r["end"], r["reward"] * UNIT, lt, lo, ft, fo, quoted))
print("programs simulated", len(rows), "skipped", dict(skipped))

def tstat(xs):
    n = len(xs)
    if n < 2: return 0.0
    mu = sum(xs) / n; sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))
    return mu / (sd / math.sqrt(n)) if sd else 0.0

def day_t(rs, f):
    byd = defaultdict(list)
    for r in rs: byd[r[1] // 86400].append(f(r))
    return tstat([sum(v) / len(v) for v in byd.values()]), len(byd)

print(f"\n{'category':22s} {'n':>4s} {'pool$':>9s} {'loss$ thr':>10s} {'loss$ tch':>10s} {'s* tch':>8s} {'share obs':>9s}  halves (mean net/program $, day-t) under touch   verdict")
for c in sorted({r[0] for r in rows}, key=lambda c: -sum(1 for r in rows if r[0] == c)):
    rs = [r for r in rows if r[0] == c]
    pool = sum(r[2] for r in rs); lt = sum(r[3] for r in rs); lo = sum(r[4] for r in rs)
    sh = obs_share.get(c)
    sstar = lo / pool if pool else float("inf")
    out, ok = [], sh is not None
    for name, sel in (("H1", lambda r: r[1] < SPLIT), ("H2", lambda r: r[1] >= SPLIT)):
        h = [r for r in rs if sel(r)]
        if not h or sh is None: out.append(f"{name}: n/a"); ok = False; continue
        net = lambda r: r[2] * sh - r[4]
        mu = sum(net(r) for r in h) / len(h); dtt, nd = day_t(h, net)
        out.append(f"{name}: {mu:+.2f} t={dtt:.2f} ({nd}d)")
        ok &= mu > 0 and dtt >= 2
    ok &= sh is not None and sstar < sh
    print(f"{c:22s} {len(rs):4d} {pool:9.0f} {lt:10.0f} {lo:10.0f} {sstar:8.3f} {sh if sh is not None else float('nan'):9.3f}  {' | '.join(out)}   {'PASS' if ok else 'fail'}")
print("\nfills per quoted minute: through %.4f touch %.4f" % (sum(r[5] for r in rows) / sum(r[7] for r in rows), sum(r[6] for r in rows) / sum(r[7] for r in rows)))
print("observed share samples:", {c: len(v) for c, v in shares.items()})
