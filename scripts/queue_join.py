"""Queue-counted touch-maker on Kalshi, from a `sportsbot queue-log` db.

Pre-registered in docs/QUEUE_ADJUSTED_TOUCH_PREREG_2026-10-19.md. This is
the committed version of Result 15 addendum 3's join-level check.

    python3 scripts/queue_join.py data/queue_2026-10-19.sqlite

A join is a $1 resting order on one side of a game's Kalshi book (prices
in the queue study's US-long frame):
- JOIN-QUEUE: at the touch, behind the displayed size there. It fills
  once later prints at or through it exceed that size (cancellations
  ahead ignored, so conservative).
- NEW-PRICE: at a sample whose touch moved to a new price; nothing ahead.
- FRONT: the same timing as JOIN-QUEUE, but filled on the first print at
  or through, i.e. the front-of-queue upper bound (a check vs Result 24).
Deadline: 30 min, never past the start. CLV is the last pre-start mid, in
the side's frame, minus the fill price, minus the Kalshi maker fee.
"""

from __future__ import annotations

import random
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, ".")
from sportsbot.exchanges.kalshi import kalshi_maker_fee_per_share  # noqa: E402

SPACING_S = 300.0
DEADLINE_S = 1800.0
EPS = 1e-9


def joins_for_game(samples, prints, start, ticker):
    """samples: [(ts, bid, ask, bid_qty, ask_qty)], prints: [(ts, price, size)],
    both pre-start and sorted. Returns join dicts with 'kind', 'filled', 'clv'."""
    if not samples:
        return []
    last = samples[-1]
    if last[1] is None or last[2] is None:
        return []
    close = (last[1] + last[2]) / 2.0
    out = []
    for side in ("bid", "ask"):
        last_join = {"JOIN-QUEUE": -1e18, "NEW-PRICE": -1e18}
        prev_level = None
        for ts, bid, ask, bq, aq in samples:
            level, qty = (bid, bq) if side == "bid" else (ask, aq)
            if level is None or not (0.0 < level < 1.0):
                prev_level = None
                continue
            kinds = []
            if ts - last_join["JOIN-QUEUE"] >= SPACING_S:
                kinds += [("JOIN-QUEUE", float(qty or 0.0)), ("FRONT", 0.0)]
                last_join["JOIN-QUEUE"] = ts
            if (prev_level is not None and abs(level - prev_level) > EPS
                    and ts - last_join["NEW-PRICE"] >= SPACING_S):
                kinds.append(("NEW-PRICE", 0.0))
                last_join["NEW-PRICE"] = ts
            prev_level = level
            for kind, queue in kinds:
                filled = _fills(prints, ts, min(ts + DEADLINE_S, start), side, level, queue)
                row = {"kind": kind, "filled": filled, "clv": None}
                if filled:
                    if side == "bid":            # long the US-long side at `level`
                        price = level
                        clv = close - level
                    else:                        # short = buy the other side at 1 - level
                        price = 1.0 - level
                        clv = level - close
                    row["clv"] = clv - kalshi_maker_fee_per_share(ticker or "", price=price)
                out.append(row)
    return out


def _fills(prints, t0, t1, side, level, queue):
    cum = 0.0
    for ts, price, size in prints:
        if ts <= t0:
            continue
        if ts >= t1:
            break
        through = price <= level + EPS if side == "bid" else price >= level - EPS
        if through:
            cum += float(size)
            if cum > queue + EPS:
                return True
    return False


def boot(by_game, n=2000, seed=19):
    keys = [k for k, v in by_game.items() if v]
    if len(keys) < 2:
        return None
    allv = [x for k in keys for x in by_game[k]]
    est = sum(allv) / len(allv)
    rng = random.Random(seed)
    ms = []
    for _ in range(n):
        s = [x for _ in keys for x in by_game[rng.choice(keys)]]
        ms.append(sum(s) / len(s))
    ms.sort()
    return est, ms[int(.025 * n)], ms[int(.975 * n) - 1]


def analyse(db_path):
    conn = sqlite3.connect(db_path)
    games = conn.execute("SELECT game_id, sport, start_ts, k_ticker FROM games WHERE k_ticker IS NOT NULL").fetchall()
    rows = defaultdict(list)   # (kind, sport) -> [(game, join)]
    for gid, sport, start, ticker in games:
        samples = conn.execute(
            "SELECT ts, bid, ask, bid_qty, ask_qty FROM samples WHERE game_id=? AND venue='kalshi'"
            " AND ts < ? ORDER BY ts", (gid, start)).fetchall()
        prints = conn.execute(
            "SELECT ts, price, size FROM prints WHERE game_id=? AND venue='kalshi' AND ts < ?"
            " ORDER BY ts", (gid, start)).fetchall()
        for j in joins_for_game(samples, prints, start, ticker):
            rows[(j["kind"], sport)].append((gid, j))
            rows[(j["kind"], "ALL")].append((gid, j))
    report = {}
    for (kind, sport), items in rows.items():
        fills = defaultdict(list)
        for gid, j in items:
            if j["filled"]:
                fills[gid].append(j["clv"])
        nf = sum(len(v) for v in fills.values())
        report[(kind, sport)] = {"joins": len(items), "fills": nf,
                                 "fill_rate": nf / len(items) if items else None,
                                 "games": len(fills), "clv": boot(fills)}
    return report


def verdict(report):
    r = report.get(("JOIN-QUEUE", "ALL"))
    if not r or r["clv"] is None:
        return "INCONCLUSIVE"
    est, lo, hi = r["clv"]
    if r["games"] >= 15 and lo > 0:
        return "PASS"
    if hi <= 0:
        return "FAIL"
    return "INCONCLUSIVE"


def main():
    rep = analyse(sys.argv[1])
    for kind in ("JOIN-QUEUE", "NEW-PRICE", "FRONT"):
        for (k, sport), r in sorted(rep.items(), key=lambda x: (x[0][1] != "ALL", x[0][1])):
            if k != kind:
                continue
            c = r["clv"]
            fr = "—" if r["fill_rate"] is None else f"{r['fill_rate']:.1%}"
            cs = "—" if c is None else f"{c[0]*100:+.2f}c [{c[1]*100:+.2f}, {c[2]*100:+.2f}]"
            print(f"{kind:10s} {sport:16s} joins {r['joins']:5d} fills {r['fills']:5d} ({fr}) "
                  f"games {r['games']:3d}  net CLV/fill {cs}")
    print("VERDICT (JOIN-QUEUE, all sports):", verdict(rep))


if __name__ == "__main__":
    main()
