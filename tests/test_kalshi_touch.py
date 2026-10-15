"""Kalshi touch-maker baseline: rest placement on both sides, the
front-of-queue vs strict-through fill bounds, the rail-refused close, and
CLV net of the maker fee."""
from datetime import datetime, timezone

from sportsbot.backtest.kalshi_market import MarketGame
from sportsbot.backtest.kalshi_touch import Print, close_yes, filled, rest_for, run
from sportsbot.core.types import Side

S = 10_000.0


def test_rest_one_tick_under_the_taker_on_its_side():
    assert rest_for(Print(0, 0.55, True)) == (Side.YES, 0.54)
    # a NO taker at YES 0.55 paid 0.45 for NO; the rest is a NO bid at 0.44
    assert rest_for(Print(0, 0.55, False)) == (Side.NO, 0.44)


def test_front_of_queue_fills_at_the_level_strict_needs_a_print_through_it():
    tape = [Print(0, 0.55, True), Print(10, 0.54, False), Print(20, 0.53, False)]
    assert filled(tape, 0, S, Side.YES, 0.54, None, strict=False)
    assert filled(tape, 0, S, Side.YES, 0.54, None, strict=True)        # 0.53 went through
    assert not filled(tape[:2], 0, S, Side.YES, 0.54, None, strict=True)
    assert not filled(tape, 0, S, Side.YES, 0.54, 5.0, strict=False)    # past the deadline
    assert not filled(tape, 0, 10, Side.YES, 0.54, None, strict=False)  # game started
    # NO bid at 0.44 = YES ask at 0.56: met by YES prints at or above it
    up = [Print(0, 0.55, False), Print(5, 0.56, True), Print(6, 0.57, True)]
    assert filled(up, 0, S, Side.NO, 0.44, None, strict=False)
    assert filled(up, 0, S, Side.NO, 0.44, None, strict=True)
    assert not filled(up[:2], 0, S, Side.NO, 0.44, None, strict=True)


def test_close_is_the_last_pre_start_print_refused_at_the_rail():
    tape = [Print(0, 0.50, True), Print(5, 0.60, True), Print(20, 0.99, True)]
    assert close_yes(tape, 10) == 0.60
    assert close_yes(tape, 30) is None


def test_run_scores_fills_against_the_close_net_of_the_fee():
    g = MarketGame(date=datetime(2026, 9, 1, tzinfo=timezone.utc), home="h", away="a",
                   home_ticker="T", close_ts=int(S), home_won=True, start_ts=100)
    tape = [Print(0, 0.55, True), Print(10, 0.54, False), Print(20, 0.53, False),
            Print(30, 0.58, True)]
    res = run([g], {"T": tape}, lambda p, t: 0.001)
    front = res[("rest to start", "front")]
    # three rests fill: the YES bid at 0.54 earns +0.04 to the 0.58 close;
    # the two NO bids (YES asks 0.55 / 0.54) are run over by the rally and
    # lose 0.03 / 0.04 -- adverse selection is in the number
    assert res["prints"] == 4 and front["fill_rate"] == 0.75
    assert abs(front["gross"]["mean"] - (0.04 - 0.03 - 0.04) / 3) < 1e-9
    assert abs(front["net"]["mean"] - (front["gross"]["mean"] - 0.001)) < 1e-12


def test_start_is_the_earlier_of_ticker_and_schedule_and_unmatched_games_drop():
    from sportsbot.backtest.kalshi_touch import anchor_starts

    def g(home, away, ts):
        return MarketGame(date=datetime(2026, 10, 12, tzinfo=timezone.utc), home=home,
                          away=away, home_ticker=home, close_ts=ts + 99_999,
                          home_won=True, start_ts=ts)

    t = 1_791_849_600    # the ticker's 20:00 ET
    sched = [{"pk": 1, "ts": t - 3 * 3600, "home": "milwaukee brewers",
              "away": "los angeles dodgers"}]
    out = anchor_starts([g("milwaukee brewers", "los angeles dodgers", t),
                         g("boston red sox", "chicago cubs", t)], sched)
    assert [(x.home, x.start_ts) for x in out] == [("milwaukee brewers", t - 3 * 3600)]
    # a schedule later than the ticker never moves the anchor later
    late = [{**sched[0], "ts": t + 600}]
    assert anchor_starts([g("milwaukee brewers", "los angeles dodgers", t)], late)[0].start_ts == t
