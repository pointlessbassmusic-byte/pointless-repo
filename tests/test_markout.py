"""Mark-out on maker fills: the test for the one non-predictive edge.

Each case pins a way passive quoting can look profitable when it is not.
"""

from sportsbot.backtest.markout import (
    Fill,
    effective_spread,
    markout,
    verdict,
)

FEE = 0.0025


def _tape(spec, start=0.0, step=10.0):
    """spec: [(taker_book_side, price), ...] at fixed intervals."""
    return [Fill(ts=start + i * step, price=p, taker_book_side=s)
            for i, (s, p) in enumerate(spec)]


def test_taker_book_side_maps_to_the_right_resting_order():
    """'ask' means the taker sold, so a resting BID filled and the maker is
    LONG. Getting this backwards flips the sign of every conclusion."""
    assert Fill(0, 0.5, "ask").maker_bought is True
    assert Fill(0, 0.5, "bid").maker_bought is False
    assert Fill(0, 0.5, "unknown").maker_bought is None


def test_a_maker_picked_off_shows_negative_markout():
    # maker buys at 0.50, price then walks down
    tape = _tape([("ask", 0.50), ("ask", 0.48), ("ask", 0.46)])
    s = markout(tape, horizon_s=30, maker_fee=FEE)
    assert s.mean < 0 and s.median < 0
    assert not s.pays


def test_a_maker_paid_the_spread_shows_positive_markout():
    tape = _tape([("ask", 0.50), ("ask", 0.52), ("ask", 0.54)])
    s = markout(tape, horizon_s=30, maker_fee=FEE)
    assert s.mean > 0 and s.pays


def test_zero_markout_still_loses_the_fee():
    """The typical fill capturing nothing is not break-even: makers are
    charged on Kalshi's sports series."""
    tape = _tape([("ask", 0.50)] * 6)
    s = markout(tape, horizon_s=30, maker_fee=FEE)
    assert s.median == 0.0
    assert s.net_median == -FEE
    assert not s.pays
    assert "NO EDGE" in verdict([s], half_spread=0.005)


def test_one_sided_flow_is_called_out():
    """78/22 flow means a two-sided quote accumulates inventory instead of
    staying flat, and pooled means become drift rather than capture."""
    tape = _tape([("bid", 0.50)] * 8 + [("ask", 0.50)] * 2)
    s = markout(tape, horizon_s=30, maker_fee=FEE)
    assert not s.balanced
    assert s.buy_share < 0.45
    assert "ONE-SIDED" in verdict([s], half_spread=0.005)


def test_balanced_flow_is_not_flagged():
    tape = _tape([("bid", 0.50), ("ask", 0.50)] * 5)
    s = markout(tape, horizon_s=30, maker_fee=FEE)
    assert s.balanced
    assert "ONE-SIDED" not in verdict([s], half_spread=0.005)


def test_a_fat_tail_does_not_pass_as_an_edge():
    """A positive mean carried by one winner, with a zero median, is not a
    market-making business. `pays` is median-based for exactly this."""
    # horizon 15s with 10s steps: each fill marks to its immediate
    # successor, so only the last one sees the jump.
    tape = _tape([("ask", 0.50)] * 20 + [("ask", 0.90)])
    s = markout(tape, horizon_s=15, maker_fee=FEE)
    assert s.mean > 0
    assert s.median == 0.0
    assert not s.pays


def test_block_trades_are_excluded():
    """Negotiated blocks are not book fills and must not count as maker
    prints."""
    tape = _tape([("ask", 0.50), ("ask", 0.60)])
    tape[0].is_block = True
    assert markout(tape, horizon_s=30, maker_fee=FEE) is None


def test_effective_spread_uses_taker_side_flips():
    tape = _tape([("bid", 0.51), ("ask", 0.50), ("bid", 0.51), ("ask", 0.50)])
    assert abs(effective_spread(tape) - 0.01) < 1e-9


def test_markout_never_marks_to_a_trade_beyond_the_horizon():
    tape = [Fill(0, 0.50, "ask"), Fill(10, 0.60, "ask"), Fill(1000, 0.90, "ask")]
    s = markout(tape, horizon_s=30, maker_fee=FEE)
    # only the t=10 print is inside the horizon for the first fill
    assert abs(s.mean - 0.10) < 1e-9


def test_too_few_fills_says_so_rather_than_guessing():
    assert markout([Fill(0, 0.5, "ask")], 60, FEE) is None
    assert "NO DATA" in verdict([None], half_spread=None)
