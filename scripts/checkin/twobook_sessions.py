"""Two-book lead-lag, per logged session and pooled (EDGE_VERDICT Result 10).

    python3 scripts/checkin/twobook_sessions.py [data/twobook.sqlite] [--sport tennis]

A session is a run of book rows with no gap longer than SESSION_GAP_S. Each
session is re-run on its own rows only ([start, end]): both venues' mids on a
30 s grid (`twobook._grid`), then for k steps ahead

    Polymarket->Kalshi: d(PM mid over k) on (Kalshi mid - PM mid) now
    Kalshi->Polymarket: d(Kalshi mid over k) on (PM mid - Kalshi mid) now

each an OLS slope with standard errors clustered by pair (CR0 sandwich).
The latest session is printed at k = 1, 3, 6, 15 with its gap level and
activity; every session at k = 3 (90 s); then the inverse-variance pool at
90 s with t to three decimals and each session's share of the weight.

Rebuilt 2026-11-05 after a container reset lost the uncommitted daily
scripts and the 27-session database: the pool restarts with the sessions in
the database it is given.
"""
from __future__ import annotations

import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sportsbot.substrate_bridge.twobook import _grid  # noqa: E402

STEP_S = 30.0
SESSION_GAP_S = 600.0
MIN_GRID = 20
HORIZONS = (1, 3, 6, 15)
POOL_K = 3
# The 27 sessions logged Oct 6 - Nov 4 were lost with the sandbox on Nov 5;
# their pool survives only as the published summary (EDGE_VERDICT Result 10,
# note 2026-11-05), carried forward by inverse-variance combination.
PRIOR = {"label": "27 sessions Oct 6 - Nov 4 (published)",
         "p2k": (0.0105, 0.0034), "k2p": (0.0090, 0.0035)}


def sessions(conn) -> list[tuple[float, float]]:
    """[start, end] of each run of book rows with no gap over SESSION_GAP_S."""
    out: list[list[float]] = []
    for (ts,) in conn.execute("SELECT ts FROM books ORDER BY ts"):
        if out and ts - out[-1][1] <= SESSION_GAP_S:
            out[-1][1] = ts
        else:
            out.append([ts, ts])
    return [(a, b) for a, b in out]


def clustered_slope(x: list[float], y: list[float], groups: list) -> tuple[float, float]:
    """OLS slope of y on x (with intercept), standard error clustered by group."""
    n = len(x)
    if n < 3:
        return float("nan"), float("nan")
    mx, my = statistics.mean(x), statistics.mean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx <= 0:
        return float("nan"), float("nan")
    b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / sxx
    score: dict = defaultdict(float)
    for a, c, g in zip(x, y, groups):
        score[g] += (a - mx) * ((c - my) - b * (a - mx))
    return b, math.sqrt(sum(s * s for s in score.values())) / sxx


def session_grids(src, start: float, end: float, sport: str | None, step: float = STEP_S) -> dict:
    """Each usable pair's grid built from this session's rows alone."""
    conn = sqlite3.connect(":memory:")
    src.backup(conn)
    conn.execute("DELETE FROM books WHERE ts < ? OR ts > ?", (start, end))
    grids = {}
    for pid, sp in conn.execute("SELECT pair_id, sport FROM pairs").fetchall():
        if sport and sp != sport:
            continue
        g = _grid(conn, pid, step)
        if len(g) >= MIN_GRID:
            grids[pid] = g
    conn.close()
    return grids


def estimate(grids: dict, k: int) -> dict:
    gap, dpm, dk, grp = [], [], [], []
    for pid, g in grids.items():
        for i in range(len(g) - k):
            _, kb, ka, pb, pa = g[i]
            _, kb2, ka2, pb2, pa2 = g[i + k]
            km, pm = (kb + ka) / 2, (pb + pa) / 2
            gap.append(km - pm)
            dpm.append((pb2 + pa2) / 2 - pm)
            dk.append((kb2 + ka2) / 2 - km)
            grp.append(pid)
    if len(gap) < 10:
        return {"n": len(gap)}
    p2k = clustered_slope(gap, dpm, grp)
    k2p = clustered_slope([-x for x in gap], dk, grp)
    return {"n": len(gap), "gap_sd": statistics.pstdev(gap), "p2k": p2k, "k2p": k2p}


def activity(grids: dict) -> tuple[float, float, int]:
    mk = mp = tot = 0
    for g in grids.values():
        for i in range(1, len(g)):
            tot += 1
            mk += (g[i][1] + g[i][2]) != (g[i - 1][1] + g[i - 1][2])
            mp += (g[i][3] + g[i][4]) != (g[i - 1][3] + g[i - 1][4])
    return (mk / tot if tot else 0.0), (mp / tot if tot else 0.0), tot


def pool(vals: list[tuple[float, float]]) -> tuple[float, float]:
    w = [(b, 1 / (s * s)) for b, s in vals if s == s and s > 0 and b == b]
    if not w:
        return float("nan"), float("nan")
    tw = sum(x for _, x in w)
    return sum(b * x for b, x in w) / tw, math.sqrt(1 / tw)


def _fmt(bs: tuple[float, float]) -> str:
    b, s = bs
    t = b / s if s and s == s and s > 0 else float("nan")
    return f"{b:+.3f} ± {s:.3f} (t {t:+.1f})"


def main(db: str = "data/twobook.sqlite", sport: str | None = "tennis") -> None:
    src = sqlite3.connect(db)
    spans = sessions(src)
    if not spans:
        print("no sessions logged")
        return
    rows = []
    for start, end in spans:
        grids = session_grids(src, start, end, sport)
        est = estimate(grids, POOL_K)
        label = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%m-%d %H:%M")
        rows.append((label, start, end, grids, est))
    label, start, end, grids, _ = rows[-1]
    mk, mp, tot = activity(grids)
    print(f"latest session {label}Z, {(end - start) / 60:.1f} min, usable pairs {len(grids)} ({sport or 'all'})")
    print(f"activity: Kalshi mid moved in {mk:.1%}, Polymarket in {mp:.1%} of {tot} steps")
    for k in HORIZONS:
        e = estimate(grids, k)
        if "p2k" in e:
            print(f"k={k:2d} ({k * STEP_S:4.0f}s) n={e['n']:5d} gap sd {e['gap_sd']:.4f}  "
                  f"Polymarket->Kalshi {_fmt(e['p2k'])}   Kalshi->Polymarket {_fmt(e['k2p'])}")
    gaps = [abs((kb + ka) / 2 - (pb + pa) / 2) for g in grids.values() for _, kb, ka, pb, pa in g]
    if gaps:
        gs = sorted(gaps)
        print(f"|gap| mean {statistics.mean(gaps):.4f}, p90 {gs[int(0.9 * (len(gs) - 1))]:.4f}, "
              f">0.02 in {sum(g > 0.02 for g in gaps) / len(gaps):.1%}")
    print()
    for label, _, _, grids, e in rows:
        if "p2k" in e:
            print(f"{label} pairs {len(grids):3d}  P->K {_fmt(e['p2k'])}   K->P {_fmt(e['k2p'])}")
        else:
            print(f"{label} pairs {len(grids):3d}  (too few grid points)")
    for key, name in (("p2k", "P->K"), ("k2p", "K->P")):
        vals = [(r[0], r[4][key]) for r in rows if key in r[4]]
        b, s = pool([v for _, v in vals])
        n_sig = sum(1 for _, (bb, ss) in vals if ss and ss > 0 and bb / ss >= 2)
        print(f"pooled {name} {POOL_K * STEP_S:.0f} s over {len(vals)} sessions: {b:+.4f} ± {s:.4f} "
              f"(t {b / s:+.3f}); sessions with t >= 2: {n_sig}")
        w = [(1 / (ss * ss), lab) for lab, (bb, ss) in vals if ss and ss > 0]
        tw = sum(x for x, _ in w) or 1.0
        print("  weight shares: " + ", ".join(f"{lab} {x / tw:.0%}" for x, lab in sorted(w, reverse=True)[:3]))
        cb, cs = pool([PRIOR[key]] + [v for _, v in vals])
        print(f"  combined with {PRIOR['label']}: {cb:+.4f} ± {cs:.4f} (t {cb / cs:+.2f}); "
              f"new sessions' weight {1 - (1 / PRIOR[key][1] ** 2) / (1 / cs ** 2):.2%}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sp = "tennis"
    if "--sport" in sys.argv:
        sp = sys.argv[sys.argv.index("--sport") + 1]
        args = [a for a in args if a != sp]
    main(args[0] if args else "data/twobook.sqlite", None if sp == "all" else sp)
