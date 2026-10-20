"""scripts/queue_fill_model.py: the pre-registered touch fill-share model."""

import importlib.util
import math
import sqlite3
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "queue_fill_model", Path(__file__).resolve().parents[1] / "scripts" / "queue_fill_model.py")
qf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qf)

DAY = 86400.0


def test_poisson_tail_matches_the_closed_form():
    assert qf.poisson_tail(2.0, 0) == 1.0
    assert qf.poisson_tail(0.0, 1) == 0.0
    assert qf.poisson_tail(2.0, 1) == pytest.approx(1 - math.exp(-2.0))
    assert qf.poisson_tail(2.0, 3) == pytest.approx(1 - math.exp(-2.0) * (1 + 2 + 2))


def test_fill_prob_falls_with_the_queue_ahead():
    # empty queue needs one typical print, a 10-print queue needs eleven
    assert qf.fill_prob(1.0, 10.0, 10.0, 0.0) == pytest.approx(1 - math.exp(-1.0))
    assert qf.fill_prob(1.0, 10.0, 10.0, 100.0) < 1e-6
    assert qf.fill_prob(None, 10.0, 10.0, 0.0) is None


def test_touch_runs_drop_censored_and_gapped_runs():
    s = [(0, 0.40, 0.42, 1, 1), (30, 0.40, 0.42, 1, 1), (60, 0.41, 0.42, 1, 1),
         (90, 0.41, 0.42, 1, 1), (400, 0.41, 0.42, 1, 1), (430, 0.42, 0.43, 1, 1)]
    # bid: 0.40 lives 60 s; 0.41 is cut by the 310 s gap; the run from 400 lives 30 s; last run censored
    assert qf.touch_runs(s, "bid") == [60, 30]
    assert qf.touch_runs(s, "ask") == [430 - 400]


def test_through_size_uses_the_touch_at_each_print():
    s = [(0, 0.40, 0.42, 1, 1), (100, 0.38, 0.42, 1, 1)]
    prints = [(50, 0.40, 5.0), (150, 0.39, 7.0), (160, 0.38, 3.0)]
    assert qf.through_size(s, prints, "bid", -1, 200) == 8.0      # 0.39 is above the 0.38 touch


def _db(path, n_days, quiet_from_day=None):
    """One NHL game per day: a 2-hour pre-start book, bid 0.40 / ask 0.42, the bid
    queue alternating thin (5) and deep (5000) every 5 min, prints at the bid."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE games (game_id INTEGER, sport TEXT, start_ts REAL, k_ticker TEXT);
        CREATE TABLE samples (ts REAL, game_id INTEGER, venue TEXT, bid REAL, ask REAL,
                              bid_qty REAL, ask_qty REAL);
        CREATE TABLE prints (game_id INTEGER, ts REAL, price REAL, size REAL, venue TEXT);""")
    for d in range(n_days):
        t0 = 1_800_000_000 // 86400 * 86400 + d * DAY + 3600
        start = t0 + 7200
        conn.execute("INSERT INTO games VALUES (?,?,?,?)", (d, "nhl", start, f"KXNHLGAME-{d}-A"))
        for k in range(240):
            ts = t0 + 30 * k
            bid = 0.40 if (k // 20) % 2 == 0 else 0.41          # touch moves every 10 min
            qty = 5.0 if (k // 10) % 2 == 0 else 5000.0
            conn.execute("INSERT INTO samples VALUES (?,?,?,?,?,?,?)", (ts, d, "kalshi", bid, 0.42, qty, 50.0))
            if quiet_from_day is None or d < quiet_from_day:
                conn.execute("INSERT INTO prints VALUES (?,?,?,?,?)", (d, ts + 15, bid, 10.0, "kalshi"))
    conn.commit()
    return conn


def test_burn_in_days_are_never_scored_and_thin_queues_rank_first(tmp_path):
    conn = _db(tmp_path / "q.sqlite", 10)
    rows = qf.score(qf.load(conn))
    assert rows and min(r["day"] for r in rows) == qf.BURN_IN_DAYS
    thin = [r["p"] for r in rows if r["filled"]]
    deep = [r["p"] for r in rows if not r["filled"]]
    assert thin and deep and min(thin) > max(deep)
    rep = qf.decide(rows)
    assert rep["share_top"] == 1.0


def test_a_window_with_no_scored_joins_fails(tmp_path):
    conn = _db(tmp_path / "q.sqlite", 5)                         # all burn-in
    assert qf.score(qf.load(conn)) == []
    assert qf.decide([])["verdict"] == "FAIL"


def test_decide_needs_the_lift_and_no_worse_clv():
    base = [{"game": g, "p": 0.01, "filled": False, "clv": None} for g in range(600)]
    base += [{"game": 1000 + g, "p": 0.001, "filled": True, "clv": 0.01} for g in range(5)]
    good = [{"game": 2000 + g, "p": 0.9, "filled": True, "clv": 0.012} for g in range(10)]
    meh = [{"game": 3000 + g, "p": 0.5, "filled": g < 2, "clv": 0.012 if g < 2 else None} for g in range(10)]
    rep = qf.decide(base + good + meh)
    assert rep["share_top"] > 3 * rep["share_all"]
    assert rep["verdict"] == "PASS"
    bad = [dict(r, clv=-0.05) for r in good]
    rep = qf.decide(base + bad + meh)
    assert rep["share_top"] > 3 * rep["share_all"]
    assert rep["verdict"] == "FAIL"                             # the predictable fills are the adverse ones
