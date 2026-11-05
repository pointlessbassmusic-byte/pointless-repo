"""scripts/checkin/twobook_sessions.py: the daily two-book estimator, rebuilt
2026-11-05 after a container reset lost the uncommitted scripts."""

import importlib.util
import math
import random
import sqlite3
import time
from pathlib import Path

from sportsbot.substrate_bridge import twobook as tb

_spec = importlib.util.spec_from_file_location(
    "twobook_sessions", Path(__file__).resolve().parents[1] / "scripts" / "checkin" / "twobook_sessions.py")
ts_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts_mod)


def _db(path, sessions=((1_800_000_000.0, 300),), pairs=4, lag_steps=3, step=10.0):
    """Polymarket's book equals Kalshi's `lag_steps` samples ago, on several
    pairs, in one or more sessions separated by long gaps."""
    random.seed(11)
    conn = sqlite3.connect(path)
    conn.executescript(tb.SCHEMA)
    for pid in range(1, pairs + 1):
        conn.execute("INSERT INTO pairs VALUES (?,'tennis',?,'A','B','c','t','A','B',1,?)",
                     (pid, f"KX-{pid}", time.time()))
    rows = []
    for t0, n in sessions:
        for pid in range(1, pairs + 1):
            mids = [0.5]
            for _ in range(n - 1):
                mids.append(min(0.9, max(0.1, mids[-1] + random.choice((-0.01, 0.0, 0.0, 0.01)))))
            for i, m in enumerate(mids):
                t = t0 + i * step
                rows.append((t, pid, "kalshi", round(m - 0.005, 4), round(m + 0.005, 4), 100, 100))
                pm = mids[max(0, i - lag_steps)]
                rows.append((t + 0.5, pid, "polymarket", round(pm - 0.005, 4), round(pm + 0.005, 4), 100, 100))
    conn.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_sessions_split_on_long_gaps(tmp_path):
    db = str(tmp_path / "tb.sqlite")
    _db(db, sessions=((1_800_000_000.0, 120), (1_800_090_000.0, 120)))
    spans = ts_mod.sessions(sqlite3.connect(db))
    assert len(spans) == 2 and spans[0][1] < spans[1][0]


def test_finds_the_lag_in_the_lagging_direction_only(tmp_path):
    db = str(tmp_path / "tb.sqlite")
    _db(db, lag_steps=3)
    src = sqlite3.connect(db)
    (start, end), = ts_mod.sessions(src)
    grids = ts_mod.session_grids(src, start, end, "tennis")
    assert len(grids) == 4
    e = ts_mod.estimate(grids, ts_mod.POOL_K)
    b, se = e["p2k"]
    assert b > 0.5 and b / se > 3                     # Polymarket closes the gap
    b2, se2 = e["k2p"]
    assert abs(b2) < 3 * se2 + 0.05                   # Kalshi does not follow


def test_pool_is_inverse_variance_and_clusters_by_pair():
    b, s = ts_mod.pool([(0.1, 0.1), (0.3, 0.1)])
    assert math.isclose(b, 0.2) and math.isclose(s, 0.1 / math.sqrt(2))
    # one pair's points carry no independent information about another's:
    # duplicating every point inside its own cluster leaves the clustered se
    # at the original size, where plain OLS would shrink it by sqrt(2)
    x = [0.0, 1.0, 2.0, 3.0, 0.5, 1.5, 2.5, 3.5]
    y = [0.1, 0.9, 2.2, 2.8, 0.4, 1.7, 2.4, 3.6]
    g = [1, 1, 1, 1, 2, 2, 2, 2]
    b1, s1 = ts_mod.clustered_slope(x, y, g)
    b2, s2 = ts_mod.clustered_slope(x + x, y + y, g + g)
    assert math.isclose(b1, b2) and math.isclose(s1, s2, rel_tol=1e-9)


def test_prior_summary_combines_like_one_more_session():
    """The lost 27-session pool is carried by its published summary; adding
    Nov 5's session to it gives the figures in the 2026-11-05 note."""
    b, s = ts_mod.pool([ts_mod.PRIOR["p2k"], (0.2364, 0.1250)])
    assert round(b, 4) == 0.0107 and round(s, 4) == 0.0034 and round(b / s, 2) == 3.14
    b, s = ts_mod.pool([ts_mod.PRIOR["k2p"], (-0.0895, 0.1009)])
    assert round(b, 4) == 0.0089 and round(b / s, 2) == 2.54
