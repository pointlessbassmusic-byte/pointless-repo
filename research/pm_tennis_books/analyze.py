"""docs/PM_TENNIS_BOOKS_PREREG_2026-10-08.md
Usage: python research/pm_tennis_books/analyze.py ATP.xlsx WTA.xlsx <pm_dir> [<kalshi_hist_dir>]
pm_dir holds markets.json + pmt/ from collect.py; kalshi_hist_dir holds kalshi_meta_cache.json + tr_hist/
(research/pinnacle_clv/collect_hist.py) for the H3 venue comparison.
Env ODDS=Avg (default) or PS; FEE=0.0695 (Polymarket US taker, default) or 0.05."""
import datetime as dt
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pinnacle_clv"))
os.environ.setdefault("ODDS", "Avg")
import run as kal  # noqa: E402  (key_td/key_full/norm/load_td/SPLIT, shared with the Kalshi test)

FEE = float(os.environ.get("FEE", "0.0695"))
SPLIT = dt.date(2026, 4, 16)
fee = lambda p: FEE * p * (1 - p)


def tstat(x):
    n = len(x)
    mu = sum(x) / n
    sd = math.sqrt(sum((v - mu) ** 2 for v in x) / (n - 1)) if n > 1 else 0.0
    return mu, (mu / (sd / math.sqrt(n)) if sd else 0.0), n


def match_row(by_player, outcomes, start_date, title):
    """Unique tennis-data row for outcome 0 vs outcome 1 within a day of the start."""
    k0, k1 = kal.key_full(outcomes[0]), kal.key_full(outcomes[1])
    if not k0 or not k1:
        return None
    c = [x for x in by_player.get(k0, []) if abs((x[0] - start_date).days) <= 1 and x[1] == k1]
    return c[0] if len(c) == 1 else None


def main(atp, wta, pm_dir, k_dir=None):
    td = kal.load_td([atp, wta])
    by_player = defaultdict(list)
    for d, w, lo, pw in td:
        by_player[w].append((d, lo, pw, True))
        by_player[lo].append((d, w, pw, False))
    mk = json.load(open(os.path.join(pm_dir, "markets.json")))
    k_meta, k_by_match = {}, {}
    if k_dir:
        k_meta = json.load(open(os.path.join(k_dir, "kalshi_meta_cache.json")))
        for t in os.listdir(os.path.join(k_dir, "tr_hist")):
            m = k_meta.get(t[:-5])
            if m:
                k_by_match[(kal.key_full(m["yes"]), m["close"][:10])] = t[:-5]
    rows, stats = [], defaultdict(int)
    for c, m in sorted(mk.items()):
        fn = os.path.join(pm_dir, "pmt", f"{c}.json")
        if not os.path.exists(fn):
            stats["no_tape"] += 1
            continue
        tr = json.load(open(fn))
        cut = m["start"] - 1800
        pre = [x for x in tr if cut - 6 * 3600 < x[0] <= cut]
        if not pre:
            stats["no_trade_6h"] += 1
            continue
        start_date = dt.datetime.fromtimestamp(m["start"], dt.timezone.utc).date()
        r = match_row(by_player, m["outcomes"], start_date, m["q"])
        if r is None:
            stats["no_unique_match"] += 1
            continue
        d, _, pw, yes_is_winner = r
        sv = m["payout"][0]
        if (sv == 1.0) != yes_is_winner:
            stats["result_mismatch"] += 1
            continue
        fair = pw if yes_is_winner else 1 - pw
        last = pre[-1][1]
        ask = next((p for t, p, s, side in reversed(pre) if side == "BUY"), None)
        bid = next((p for t, p, s, side in reversed(pre) if side == "SELL"), None)
        # H3: Kalshi last trade at the same timestamp on the same match (either side's market)
        k_last = None
        for key, sv_k in ((kal.key_full(m["outcomes"][0]), sv), (kal.key_full(m["outcomes"][1]), 1 - sv)):
            for dd in (-1, 0, 1):
                t = k_by_match.get((key, str(start_date + dt.timedelta(days=dd))))
                if t:
                    kt = [x for x in json.load(open(os.path.join(k_dir, "tr_hist", f"{t}.json"))) if x[0] <= cut]
                    if kt:
                        k_last = kt[-1][1] if key == kal.key_full(m["outcomes"][0]) else 1 - kt[-1][1]
                    break
            if k_last is not None:
                break
        rows.append((d, fair, last, ask, bid, sv, k_last))
    print("markets", len(mk), "matched", len(rows), dict(stats))
    if len(rows) < 20:
        return
    bp = [(r[2] - r[5]) ** 2 for r in rows]
    bb = [(r[1] - r[5]) ** 2 for r in rows]
    mu, t, n = tstat([a - b for a, b in zip(bp, bb)])
    print(f"H1 Brier: Polymarket {sum(bp) / n:.4f}  book-{os.environ['ODDS']} {sum(bb) / n:.4f}  "
          f"(PM minus book {mu:+.4f}, t {t:+.2f}, n {n}); median |gap| {sorted(abs(r[1] - r[2]) for r in rows)[n // 2] * 100:.1f}c")
    halves = defaultdict(list)
    for d, fair, last, ask, bid, sv, _ in rows:
        trade = None
        if ask is not None and fair - ask - fee(ask) >= 0.02:
            trade = (sv - ask - fee(ask), ask + fee(ask))
        elif bid is not None and (1 - fair) - (1 - bid) - fee(1 - bid) >= 0.02:
            trade = ((1 - sv) - (1 - bid) - fee(1 - bid), 1 - bid + fee(1 - bid))
        if trade:
            halves["H1" if d < SPLIT else "H2"].append(trade)
    res = {}
    for h in ("H1", "H2"):
        v = halves[h]
        if len(v) < 2:
            print(f"H2 {h}: trades {len(v)}")
            res[h] = (len(v), 0.0, 0.0, 0.0)
            continue
        mu, t, n = tstat([a for a, _ in v])
        res[h] = (n, mu, t, sum(a for a, _ in v) / sum(c for _, c in v))
        print(f"H2 {h}: trades {n} mean {mu * 100:+.2f}c t {t:+.2f} ROI {res[h][3]:+.3f}")
    a, b = res["H1"], res["H2"]
    ok = a[0] >= 100 and b[0] >= 100 and a[1] > 0 and b[1] > 0 and b[2] >= 2 and b[3] >= 0.01
    print("VERDICT:", "PASS (upper bound; needs forward test)" if ok else "FAIL")
    both = [r for r in rows if r[6] is not None]
    if both:
        n = len(both)
        gap = sorted(abs(r[2] - r[6]) for r in both)
        mu, t, _ = tstat([(r[2] - r[5]) ** 2 - (r[6] - r[5]) ** 2 for r in both])
        print(f"H3 same-match, same-timestamp: n {n}  Brier PM {sum((r[2] - r[5]) ** 2 for r in both) / n:.4f}  "
              f"Kalshi {sum((r[6] - r[5]) ** 2 for r in both) / n:.4f} (PM minus Kalshi {mu:+.4f}, t {t:+.2f}); "
              f"median |PM-Kalshi| {gap[n // 2] * 100:.1f}c  p90 {gap[int(n * 0.9)] * 100:.1f}c")


if __name__ == "__main__":
    main(*sys.argv[1:5])
