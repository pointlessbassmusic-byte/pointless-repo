"""Maker side of the taker tape: orientation, VPIN, and the close."""

from datetime import datetime, timezone

import pytest

from sportsbot.backtest import maker_flow as mf
from sportsbot.backtest import wallet_follow as wf
from sportsbot.backtest.polymarket_market import PMGame

START = 1_000_000


def _game(home_won=True):
    return PMGame(date=datetime.fromtimestamp(START, timezone.utc), slug="c1",
                  home="a", away="b", token="T0", start_ts=START,
                  close_ts=START + 3600, home_won=home_won, volume=1.0,
                  condition_id="c1", token_no="T1")


def _row(ts, side, price, size=10.0, asset="T0"):
    return {"ts": ts, "wallet": "w", "asset": asset, "side": side,
            "price": price, "size": size}


def test_maker_holds_the_opposite_side_at_the_takers_price():
    g = _game(home_won=False)
    tr = wf.orient([_row(START - 100, "BUY", 0.60),       # taker long home at 0.60
                    _row(START - 10, "BUY", 0.55)], g)     # close 0.55
    f = mf.maker_fills(g, tr)[0]
    assert f.price == pytest.approx(0.40)                  # maker long away at 0.40
    assert f.clv == pytest.approx(0.45 - 0.40)             # away close 0.45
    assert f.settle == pytest.approx(1.0 - 0.40)           # away won


def test_vpin_needs_a_full_window_and_reads_one_sided_flow_as_one():
    g = _game()
    rows = [_row(START - 1000 + i, "BUY", 0.50, size=2.0) for i in range(10)]
    tr = wf.orient(rows, g)
    v = mf.vpin_series(tr, buckets=10, window=3)
    assert v[:3] == [None, None, None]
    assert v[3] == pytest.approx(1.0)                      # all buys: fully imbalanced


def test_vpin_splits_a_fill_across_buckets_and_balances_two_sided_flow():
    g = _game()
    rows = []
    for i in range(10):
        rows.append(_row(START - 1000 + 2 * i, "BUY", 0.50, size=2.0))
        rows.append(_row(START - 999 + 2 * i, "SELL", 0.50, size=2.0))
    tr = wf.orient(rows, g)
    v = mf.vpin_series(tr, buckets=10, window=4)
    assert all(x is None or x == pytest.approx(0.0) for x in v)
    big = wf.orient([_row(START - 50, "BUY", 0.50, size=100.0),
                     _row(START - 40, "SELL", 0.50, size=1.0)], g)
    vb = mf.vpin_series(big, buckets=10, window=3)   # one fill fills every bucket
    assert vb[0] is None and vb[1] == pytest.approx(1.0)
