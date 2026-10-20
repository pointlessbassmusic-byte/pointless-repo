"""docs/PMUS_SURVEY_PREREG_2026-10-09.md — analysis.
Usage: python research/pmus_survey/analyze.py <scratch_dir> [<kalshi_hist_dir>]
Reads markets.json, split.json, ph/<slug>.json; optional Kalshi tapes for H3."""
import datetime as dt
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pinnacle_clv"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

CUT = 1800
FEE = 0.0695
BUCKETS = [(0.03, 0.15), (0.15, 0.35), (0.35, 0.65), (0.65, 0.85), (0.85, 0.9701)]
fee = lambda p: FEE * p * (1 - p)   # noqa: E731


def stats(x):
    n = len(x)
    if n == 0:
        return 0, 0.0, 0.0
    mu = sum(x) / n
    sd = math.sqrt(sum((v - mu) ** 2 for v in x) / (n - 1)) if n > 1 else 0.0
    return n, mu, (mu / (sd / math.sqrt(n)) if sd > 0 else 0.0)


def load(scratch):
    mk = json.load(open(os.path.join(scratch, "markets.json")))
    split = json.load(open(os.path.join(scratch, "split.json")))["median_startDate"]
    rows, drop = [], defaultdict(int)
    for slug, m in mk.items():
        fn = os.path.join(scratch, "ph", f"{slug}.json")
        if not os.path.exists(fn):
            drop["no_history"] += 1
            continue
        h = [p for p in json.load(open(fn)) if p.get("timestamp", 0) <= m["start"] - CUT
             and p.get("longPrice") is not None and p.get("shortPrice") is not None]
        if not h:
            drop["no_point_before_cut"] += 1
            continue
        last = max(h, key=lambda p: p["timestamp"])
        ask, bid = float(last["longPrice"]), 1.0 - float(last["shortPrice"])
        mid = (bid + ask) / 2
        if ask - bid > 0.10 or ask - bid < 0 or not (0.03 <= mid <= 0.97):
            drop["quote_rule"] += 1
            continue
        rows.append({"slug": slug, "group": m["group"], "league": m["league"], "mid": mid, "bid": bid,
                     "ask": ask, "settle": m["settle"], "half": "D" if m["startDate"] < split else "C",
                     "start": m["start"], "home": m["home"], "away": m["away"]})
    return rows, split, dict(drop), len(mk)


def h1(rows):
    print("\n== H1 calibration: settle rate - mid by bucket (n, diff in cents, se)")
    groups = ["ALL"] + sorted({r["group"] for r in rows})
    for grp in groups:
        sel = rows if grp == "ALL" else [r for r in rows if r["group"] == grp]
        cells = []
        for lo, hi in BUCKETS:
            b = [r for r in sel if lo <= r["mid"] < hi]
            if len(b) < 30:
                cells.append(f"[{lo:.2f},{hi:.2f}) n<30")
                continue
            diff = [r["settle"] - r["mid"] for r in b]
            n, mu, _ = stats(diff)
            se = math.sqrt(sum((d - mu) ** 2 for d in diff) / (n - 1)) / math.sqrt(n)
            cells.append(f"[{lo:.2f},{hi:.2f}) n={n} {mu * 100:+.1f}c±{se * 100:.1f}")
        print(f"{grp:13s} n={len(sel):6d} | " + " | ".join(cells))


def rules(r):
    out = []
    if r["mid"] >= 0.85:
        p = r["ask"]
        out.append(("FAV", r["settle"] - p - fee(p), p + fee(p)))
    if r["mid"] <= 0.15:
        p = 1 - r["bid"]
        out.append(("LONG-NO", (1 - r["settle"]) - p - fee(p), p + fee(p)))
        q = r["ask"]
        out.append(("DOG", r["settle"] - q - fee(q), q + fee(q)))
    return out


def h2(rows):
    print("\n== H2 taker rules at T-30 (net of 0.0695 fee): discovery -> confirmation")
    cells = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for name, pnl, cost in rules(r):
            cells[(r["group"], name)][r["half"]].append((pnl, cost))
            cells[("ALL", name)][r["half"]].append((pnl, cost))
    passes = []
    for (grp, name), halves in sorted(cells.items()):
        d, c = halves.get("D", []), halves.get("C", [])
        nd, mud, td = stats([p for p, _ in d])
        nc, muc, tc = stats([p for p, _ in c])
        roi_c = sum(p for p, _ in c) / sum(k for _, k in c) if c else 0.0
        disc = nd >= 100 and mud > 0 and td >= 2
        conf = disc and nc >= 100 and muc > 0 and tc >= 2 and roi_c >= 0.01
        flag = "PASS" if conf else ("disc-pass" if disc else "")
        print(f"{grp:13s} {name:8s} D: n={nd:5d} {mud * 100:+6.2f}c t={td:+5.2f} | "
              f"C: n={nc:5d} {muc * 100:+6.2f}c t={tc:+5.2f} ROI={roi_c:+.3f} {flag}")
        if conf and grp != "ALL":
            passes.append((grp, name))
    print("VERDICT:", f"PASS (upper bound; forward paper test required): {passes}" if passes else "FAIL")


def h3(rows, k_dir):
    import run as kal  # research/pinnacle_clv/run.py: name keys

    meta = json.load(open(os.path.join(k_dir, "kalshi_meta_cache.json")))
    k_by = {}
    for t in os.listdir(os.path.join(k_dir, "tr_hist")):
        m = meta.get(t[:-5])
        if m:
            k_by[(kal.key_full(m["yes"]), m["close"][:10])] = t[:-5]
    pairs = []
    for r in rows:
        if r["group"] not in ("tennis", "baseball"):
            continue
        day = dt.datetime.fromtimestamp(r["start"], dt.timezone.utc).date()
        cut = r["start"] - CUT
        for key, flip in ((kal.key_full(r["home"] or ""), False), (kal.key_full(r["away"] or ""), True)):
            if not key:
                continue
            hit = next((k_by.get((key, str(day + dt.timedelta(days=dd)))) for dd in (-1, 0, 1)
                        if k_by.get((key, str(day + dt.timedelta(days=dd))))), None)
            if not hit:
                continue
            tape = [x for x in json.load(open(os.path.join(k_dir, "tr_hist", f"{hit}.json"))) if x[0] <= cut]
            if tape and cut - tape[-1][0] < 7200:
                kp = 1 - tape[-1][1] if flip else tape[-1][1]
                ksv = float(meta[hit]["sv"])
                ksv = 1 - ksv if flip else ksv
                if ksv == r["settle"]:
                    pairs.append((r["mid"], kp, r["settle"], r["group"]))
            break
    print("\n== H3 Polymarket US vs Kalshi at the same T-30 (Kalshi print < 2h old, results agree)")
    for grp in ("tennis", "baseball"):
        v = [p for p in pairs if p[3] == grp]
        n = len(v)
        if n < 20:
            print(grp, "n", n)
            continue
        gap = sorted(abs(a - b) for a, b, _, _ in v)
        bp = sum((a - s) ** 2 for a, _, s, _ in v) / n
        bk = sum((b - s) ** 2 for _, b, s, _ in v) / n
        _, mu, t = stats([(a - s) ** 2 - (b - s) ** 2 for a, b, s, _ in v])
        print(f"{grp:9s} n={n} Brier PMUS {bp:.4f} Kalshi {bk:.4f} (diff {mu:+.4f}, t {t:+.2f}); "
              f"median |gap| {gap[n // 2] * 100:.1f}c p90 {gap[int(n * .9)] * 100:.1f}c")


if __name__ == "__main__":
    scratch = sys.argv[1]
    rows, split, drop, universe = load(scratch)
    print(f"universe {universe}; usable {len(rows)}; split {split}; dropped {drop}")
    from collections import Counter

    print("by group:", Counter(r["group"] for r in rows).most_common())
    h1(rows)
    h2(rows)
    if len(sys.argv) > 2:
        h3(rows, sys.argv[2])
