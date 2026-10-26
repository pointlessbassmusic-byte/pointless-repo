"""Which touch joins fill? Analysis for docs/QUEUE_FILL_MODEL_PREREG_2026-10-20.md.

    python3 scripts/queue_fill_model.py data/queue_forward.sqlite

Reads a `sportsbot queue-log` db (Kalshi books + Kalshi tape). Joins are
the JOIN-QUEUE joins of `scripts/queue_join.py`. Each is scored with

    P(fill) = P(Poisson(lambda * tau / s) >= ceil(Q / s + 1))

lambda: size printed at or through the touch per second on that side of
that game over the trailing 30 minutes; when the game has less than 30
minutes of book before the join, the sport's rate from earlier days.
tau: the sport's median touch lifetime, s: the sport's mean print size,
both from earlier days only. A sport with no earlier days uses all
sports' earlier days; with none at all the join is not scored.

Days are UTC calendar days. Day 0 is the first recorded day; days 0-6
are burn-in (fitted, never scored); joins on day 30 or later are outside
the window.
"""

from __future__ import annotations

import importlib.util
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

_spec = importlib.util.spec_from_file_location("queue_join", Path(__file__).with_name("queue_join.py"))
qj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qj)

DAY = 86400.0
TRAIL_S = 1800.0
GAP_S = 90.0          # a touch run breaks across a book gap longer than this
BURN_IN_DAYS = 7
WINDOW_DAYS = 30
EPS = 1e-9


def poisson_tail(mu, k):
    """P(N >= k) for N ~ Poisson(mu)."""
    if k <= 0:
        return 1.0
    if mu <= 0:
        return 0.0
    term = math.exp(-mu)
    cdf = term
    for i in range(1, k):
        term *= mu / i
        cdf += term
    return max(0.0, 1.0 - cdf)


def fill_prob(lam, tau, sbar, queue):
    if sbar is None or sbar <= 0 or lam is None or tau is None:
        return None
    return poisson_tail(lam * tau / sbar, math.ceil(queue / sbar + 1 - EPS))


def _level(sample, side):
    return sample[1] if side == "bid" else sample[2]


def _through(price, side, level):
    return price <= level + EPS if side == "bid" else price >= level - EPS


def touch_runs(samples, side):
    """Completed lifetimes (s) of each touch price; a run cut by a book gap or
    still alive at the last sample is censored and dropped."""
    out = []
    run_start = prev_ts = prev_level = None
    for s in samples:
        ts, level = s[0], _level(s, side)
        if level is None or not (0.0 < level < 1.0) or (prev_ts is not None and ts - prev_ts > GAP_S):
            run_start = prev_level = None
        if level is not None and 0.0 < level < 1.0:
            if prev_level is not None and abs(level - prev_level) > EPS:
                out.append(ts - run_start)
                run_start = ts
            elif run_start is None:
                run_start = ts
            prev_level = level
        prev_ts = ts
    return out


def through_size(samples, prints, side, t0, t1):
    """Size printed at or through the touch prevailing at each print, in (t0, t1]."""
    total, i = 0.0, -1
    for ts, price, size in prints:
        if ts <= t0:
            continue
        if ts > t1:
            break
        while i + 1 < len(samples) and samples[i + 1][0] <= ts:
            i += 1
        if i < 0:
            continue
        level = _level(samples[i], side)
        if level is not None and _through(price, side, level):
            total += float(size)
    return total


def day_stats(samples, prints, side):
    """(through size, observed touch seconds, print sizes, touch lifetimes)."""
    if not samples:
        return 0.0, 0.0, [], []
    seen = sum(min(b[0] - a[0], GAP_S) for a, b in zip(samples, samples[1:]))
    return (through_size(samples, prints, side, samples[0][0] - EPS, samples[-1][0]), seen,
            [float(p[2]) for p in prints], touch_runs(samples, side))


class Fit:
    """Per-(day, sport) inputs, fitted only from days strictly before."""

    def __init__(self):
        self.by_day = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, [], []]))

    def add(self, day, sport, stats):
        acc = self.by_day[day][sport]
        acc[0] += stats[0]
        acc[1] += stats[1]
        acc[2] += stats[2]
        acc[3] += stats[3]

    def params(self, day, sport):
        """(lambda, tau, sbar) for `sport` from days < `day`; pooled fallback."""
        for pick in (lambda sp: sp == sport, lambda sp: True):
            size = seen = 0.0
            prints, runs = [], []
            for d, sports in self.by_day.items():
                if d >= day:
                    continue
                for sp, (a, b, c, r) in sports.items():
                    if pick(sp):
                        size, seen = size + a, seen + b
                        prints += c
                        runs += r
            if prints and runs and seen > 0:
                return size / seen, statistics.median(runs), sum(prints) / len(prints)
        return None


def load(conn):
    games = conn.execute("SELECT game_id, sport, start_ts, k_ticker FROM games WHERE k_ticker IS NOT NULL").fetchall()
    out = []
    for gid, sport, start, ticker in games:
        samples = conn.execute(
            "SELECT ts, bid, ask, bid_qty, ask_qty FROM samples WHERE game_id=? AND venue='kalshi'"
            " AND ts < ? ORDER BY ts", (gid, start)).fetchall()
        prints = conn.execute(
            "SELECT ts, price, size FROM prints WHERE game_id=? AND venue='kalshi' AND ts < ?"
            " ORDER BY ts", (gid, start)).fetchall()
        if samples:
            out.append((gid, sport, start, ticker, samples, prints))
    return out


def score(games):
    """Scored JOIN-QUEUE joins: dicts with game, sport, day, p, filled, clv."""
    if not games:
        return []
    day0 = min(math.floor(s[0][0] / DAY) for *_, s, _p in games)
    fit = Fit()
    for gid, sport, start, ticker, samples, prints in games:
        by_day = defaultdict(list)
        for s in samples:
            by_day[math.floor(s[0] / DAY) - day0].append(s)
        for d, ss in by_day.items():
            t0, t1 = ss[0][0], ss[-1][0]
            pp = [p for p in prints if t0 - EPS < p[0] <= t1]
            for side in ("bid", "ask"):
                fit.add(d, sport, day_stats(ss, pp, side))
    out = []
    for gid, sport, start, ticker, samples, prints in games:
        first = samples[0][0]
        for j in qj.joins_for_game(samples, prints, start, ticker):
            if j["kind"] != "JOIN-QUEUE":
                continue
            day = math.floor(j["ts"] / DAY) - day0
            if day < BURN_IN_DAYS or day >= WINDOW_DAYS:
                continue
            p = fit.params(day, sport)
            if p is None:
                continue
            lam, tau, sbar = p
            if j["ts"] - first >= TRAIL_S:
                window = [s for s in samples if j["ts"] - TRAIL_S <= s[0] <= j["ts"]]
                lam = through_size(window, prints, j["side"], j["ts"] - TRAIL_S, j["ts"]) / TRAIL_S
            out.append({"game": gid, "sport": sport, "day": day, "filled": j["filled"], "clv": j["clv"],
                        "p": fill_prob(lam, tau, sbar, j["queue"])})
    return [r for r in out if r["p"] is not None]


def _clv(rows):
    by = defaultdict(list)
    for r in rows:
        if r["filled"]:
            by[r["game"]].append(r["clv"])
    return qj.boot(by), sum(len(v) for v in by.values()), len(by)


def decide(rows):
    """The pre-registered decision on all scored joins."""
    if not rows:
        return {"verdict": "FAIL", "reason": "no scored joins"}
    ranked = sorted(rows, key=lambda r: r["p"], reverse=True)
    top = ranked[:math.ceil(0.2 * len(ranked))]
    share_all = sum(r["filled"] for r in rows) / len(rows)
    share_top = sum(r["filled"] for r in top) / len(top)
    (c_all, n_all, g_all), (c_top, n_top, g_top) = _clv(rows), _clv(top)
    rep = {"joins": len(rows), "top_joins": len(top), "threshold": top[-1]["p"],
           "share_all": share_all, "share_top": share_top, "fills_all": n_all, "fills_top": n_top,
           "games_all": g_all, "games_top": g_top, "clv_all": c_all, "clv_top": c_top}
    lift = share_all > 0 and share_top > 3.0 * share_all
    clv_ok = (c_all is not None and c_top is not None
              and (c_top[0] >= c_all[0] or (c_top[1] <= c_all[2] and c_top[1] >= c_all[1])))
    rep["verdict"] = "PASS" if lift and clv_ok else "FAIL"
    return rep


def _c(c):
    return "—" if c is None else f"{c[0] * 100:+.2f}c [{c[1] * 100:+.2f}, {c[2] * 100:+.2f}]"


def main():
    rows = score(load(sqlite3.connect(sys.argv[1])))
    days = sorted({r["day"] for r in rows})
    print(f"scored joins {len(rows)} on days {days[0] if days else '—'}..{days[-1] if days else '—'}"
          f" (window days {BURN_IN_DAYS}..{WINDOW_DAYS - 1})")
    rep = decide(rows)
    if "joins" in rep:
        print(f"all joins   {rep['joins']:6d}  fill share {rep['share_all']:.2%}  fills {rep['fills_all']:4d}"
              f" games {rep['games_all']:3d}  net CLV/fill {_c(rep['clv_all'])}")
        print(f"top quintile {rep['top_joins']:5d}  fill share {rep['share_top']:.2%}  fills {rep['fills_top']:4d}"
              f" games {rep['games_top']:3d}  net CLV/fill {_c(rep['clv_top'])}  (p >= {rep['threshold']:.4f})")
    print("VERDICT:", rep["verdict"], rep.get("reason", ""))


if __name__ == "__main__":
    main()
