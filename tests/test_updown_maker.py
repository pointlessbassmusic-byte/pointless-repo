"""Maker Up/Down sets: each case pins a way a backtest of resting bids can
flatter itself."""

import pytest

from sportsbot.backtest import updown_maker as um

EP = 1_800_000_000
UP, DN = "U", "D"


def _w(rows, up_won=True):
    return um.Window(ep=EP, up_won=up_won, prints=um.orient(rows, UP, DN))


def test_orient_reads_down_prints_in_the_up_frame():
    pr = um.orient([[EP + 5, DN, "BUY", 0.70, 3], [EP + 1, UP, "SELL", 0.45, 2]], UP, DN)
    assert pr[0] == (EP + 1, 0.45, -1, 2.0)
    assert pr[1][1] == pytest.approx(0.30) and pr[1][2] == -1   # buying Down = selling Up


def test_a_print_at_the_bid_is_not_a_fill_unless_queue_front_is_assumed():
    w = _w([[EP + 10, UP, "SELL", 0.45, 5]], up_won=True)
    assert um.simulate(w, 0.45).sets == 0 and um.simulate(w, 0.45).pnl == 0.0
    o = um.simulate(w, 0.45, mode="at")
    assert o.pnl == pytest.approx(1 - 0.45) and o.unpaired == 1


def test_a_completed_set_earns_one_minus_both_legs_whatever_the_outcome():
    rows = [[EP + 10, UP, "SELL", 0.40, 5],     # through the Up bid at 0.45
            [EP + 20, UP, "BUY", 0.60, 5]]      # Up ask 0.60 > 0.55 = Down bid 0.45 filled
    for won in (True, False):
        o = um.simulate(_w(rows, up_won=won), 0.45)
        assert o.sets == 1 and o.unpaired == 0 and o.pnl == pytest.approx(0.10)


def test_the_unpaired_leg_is_held_to_resolution():
    rows = [[EP + 10, UP, "SELL", 0.40, 5]]     # Up keeps falling, Down never fills
    o = um.simulate(_w(rows, up_won=False), 0.45)
    assert o.sets == 0 and o.unpaired == 1 and o.pnl == pytest.approx(-0.45)


def test_margin_reprices_the_second_leg_to_complete_the_set():
    rows = [[EP + 10, UP, "SELL", 0.40, 5],     # Up fills at 0.45
            [EP + 20, UP, "BUY", 0.535, 5]]     # Down at 1-0.535 = 0.465 < 0.53 bid
    hold = um.simulate(_w(rows), 0.45)
    assert hold.sets == 0                       # 0.535 is not through the 0.55 Up ask
    chase = um.simulate(_w(rows), 0.45, margin=0.02)   # Down bid 1-0.45-0.02 = 0.53
    assert chase.sets == 1 and chase.pnl == pytest.approx(0.02)


def test_prints_after_the_cancel_time_or_before_latency_do_not_fill():
    late = _w([[EP + 295, UP, "SELL", 0.10, 5]])
    assert um.simulate(late, 0.45, cancel_before=10).unpaired == 0
    early = _w([[EP, UP, "SELL", 0.10, 5]])
    assert um.simulate(early, 0.45, latency=1).unpaired == 0


def test_rebate_is_reported_not_added():
    rows = [[EP + 10, UP, "SELL", 0.40, 5]]
    o = um.simulate(_w(rows, up_won=True), 0.45)
    assert o.pnl == pytest.approx(0.55)
    assert o.rebate == pytest.approx(0.2 * 0.07 * 0.45 * 0.55)
