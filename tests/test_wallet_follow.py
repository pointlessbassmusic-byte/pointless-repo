"""Wallet-following test: each case pins a way copying 'smart money' can
look profitable when it is not."""

from datetime import datetime, timezone

import pytest

from sportsbot.backtest import wallet_follow as wf
from sportsbot.backtest.polymarket_market import PMGame

START = 1_000_000


def _game(cid="c1", home_won=True, start=START, volume=1000.0):
    return PMGame(date=datetime.fromtimestamp(start, timezone.utc), slug=cid,
                  home="a", away="b", token="T0", start_ts=start,
                  close_ts=start + 3600 * 4, home_won=home_won, volume=volume,
                  condition_id=cid, token_no="T1")


def _row(ts, wallet, asset, side, price, size=10.0):
    return {"ts": ts, "wallet": wallet, "asset": asset, "side": side,
            "price": price, "size": size}


def test_orient_maps_both_tokens_into_the_home_frame():
    g = _game()
    trades = wf.orient([
        _row(1, "w", "T0", "BUY", 0.60),    # long home at 0.60
        _row(2, "w", "T1", "BUY", 0.45),    # long away = short home at 0.55
        _row(3, "w", "T1", "SELL", 0.45),   # sell away = long home at 0.55
        _row(4, "w", "T9", "BUY", 0.50),    # foreign token: dropped
    ], g)
    assert [(t.direction, round(t.p_home, 2)) for t in trades] == \
        [(1, 0.60), (-1, 0.55), (1, 0.55)]


def test_close_is_last_pre_start_print_and_refused_at_the_rail():
    g = _game()
    trades = wf.orient([_row(START - 100, "w", "T0", "BUY", 0.62),
                        _row(START + 100, "w", "T0", "BUY", 0.95)], g)
    assert wf.venue_close(trades, START) == pytest.approx(0.62)
    late = wf.orient([_row(START - 10, "w", "T0", "BUY", 0.99)], g)
    assert wf.venue_close(late, START) is None          # that is the result, not a line
    assert wf.venue_close([], START) is None


def test_copy_price_waits_for_a_later_pre_start_print_or_skips():
    g = _game()
    trades = wf.orient([_row(START - 1000, "lead", "T0", "BUY", 0.50),
                        _row(START - 990, "x", "T0", "BUY", 0.52),     # 10 s later: too soon
                        _row(START - 900, "x", "T1", "BUY", 0.45),     # 100 s later: home 0.55
                        _row(START - 10, "x", "T0", "BUY", 0.58)], g)
    assert wf.copy_price(trades, 0, delay=30, start_ts=START) == pytest.approx(0.555)
    # following a short: pay the away side of that same print
    short = wf.orient([_row(START - 1000, "lead", "T1", "BUY", 0.50),
                       _row(START - 900, "x", "T0", "BUY", 0.55)], g)
    assert wf.copy_price(short, 0, delay=30, start_ts=START) == pytest.approx(0.455)
    # nothing printed before the start: no copy, not an imagined one
    assert wf.copy_price(trades, 3, delay=30, start_ts=START) is None


def test_records_grade_each_side_against_its_own_close():
    g = _game(home_won=False)
    trades = wf.orient([_row(START - 500, "w1", "T0", "BUY", 0.50),
                        _row(START - 400, "w2", "T1", "BUY", 0.45),
                        _row(START - 100, "w3", "T0", "BUY", 0.40),
                        _row(START + 5, "w4", "T0", "BUY", 0.10)], g)
    recs = wf.records_for(g, trades)
    assert [r.wallet for r in recs] == ["w1", "w2", "w3"]        # in-play row excluded
    assert recs[0].clv == pytest.approx(0.40 - 0.50)
    # w2 bought the complement at 0.45: side price 0.45, side close 1 - 0.40
    assert recs[1].clv == pytest.approx(0.60 - 0.45)
    assert recs[0].won is False and recs[1].won is True
    assert recs[1].pnl_per_dollar == pytest.approx(0.55 / 0.45)


def test_ranking_uses_train_games_only_and_requires_history():
    train_recs = [wf.Record("hot", f"m{i}", i, 1, 0.5, 0.6, True) for i in range(25)]
    train_recs += [wf.Record("thin", f"m{i}", i, 1, 0.5, 0.9, True) for i in range(3)]
    table = wf.wallet_table(train_recs)
    assert table["hot"]["n"] == 25 and table["hot"]["mean_clv"] == pytest.approx(0.1)
    assert wf.select_wallets(table, "mean_clv", top=5, min_trades=20) == ["hot"]
    assert wf.select_wallets(table, "mean_clv", top=5, min_trades=2) == ["hot"]  # 3 markets < 5
    assert wf.select_wallets(table, "mean_clv", top=5, min_trades=2,
                             min_markets=1) == ["thin", "hot"]


def test_run_separates_informed_flow_from_copyable_flow():
    """One wallet is early to every close by 5 points; the tape reprices
    within 60 s. Its own-price CLV is real; a copier 30 s behind catches
    nothing after the spread and fee, and the test must say both."""
    games, tapes = [], {}
    for i in range(20):
        start = START + i * 86400
        g = _game(cid=f"c{i}", home_won=(i % 2 == 0), start=start)
        rows = [_row(start - 3000, "early", "T0", "BUY", 0.50),
                _row(start - 2960, f"crowd{i}", "T0", "BUY", 0.55),
                _row(start - 2900, f"crowd{i}", "T0", "BUY", 0.55),
                _row(start - 100, f"crowd{i}", "T0", "BUY", 0.55)]
        games.append(g)
        tapes[g.condition_id] = wf.orient(rows, g)
    res = wf.run(games, tapes, train_frac=0.5, top=5, min_trades=5, delay=30.0)
    d = res["by_mean_clv"]
    assert d["wallets"] == ["early"]
    assert d["own_price_clv"]["mean"] == pytest.approx(0.05)
    # copy at 0.55 + 0.005 spread + fee: strictly negative vs the 0.55 close
    assert d["copy_net_clv"]["mean"] < 0
    assert d["copies_skipped_no_later_print"] == 0
    assert res["population_test"]["n"] == 40
    text = wf.format_report(res, "baseball")
    assert "own-price CLV" in text and "copy net CLV" in text


def test_run_skips_games_whose_close_sits_at_the_rail():
    g = _game()
    tapes = {"c1": wf.orient([_row(START - 50, "w", "T0", "BUY", 0.995)], g)}
    res = wf.run([g], tapes, train_frac=0.0)
    assert res["test_trades"] == 0 and res["population_test"] is None


# ---------------------------------------------------------------------------
# Maker version: placement a post-only order could make, strict fills
# ---------------------------------------------------------------------------
def test_ask_consumed_reads_the_next_same_direction_print():
    g = _game()
    up = wf.orient([_row(START - 1000, "lead", "T0", "BUY", 0.50),
                    _row(START - 995, "x", "T0", "BUY", 0.51)], g)   # ask moved up
    assert wf.ask_consumed(up, 0) is True
    still = wf.orient([_row(START - 1000, "lead", "T0", "BUY", 0.50),
                       _row(START - 995, "x", "T0", "BUY", 0.50)], g)  # ask still at 0.50
    assert wf.ask_consumed(still, 0) is False
    quiet = wf.orient([_row(START - 1000, "lead", "T0", "BUY", 0.50),
                       _row(START - 500, "x", "T0", "BUY", 0.51)], g)  # nothing within 60 s
    assert wf.ask_consumed(quiet, 0) is None


def test_maker_fill_needs_a_print_through_the_bid_unless_queue_front_assumed():
    g = _game()
    tr = wf.orient([_row(START - 1000, "lead", "T0", "BUY", 0.50),
                    _row(START - 900, "x", "T0", "SELL", 0.49)], g)
    assert not wf.maker_fill(tr, 0, 0.49, 1, START)                 # at the bid: not through
    assert wf.maker_fill(tr, 0, 0.49, 1, START, through=False)      # front-of-queue bound
    assert wf.maker_fill(tr, 0, 0.50, 1, START)                     # 0.49 is through 0.50
    assert not wf.maker_fill(tr, 0, 0.50, 1, START - 950)           # cancelled before it
    # a short follower bids the away side: a home print at 0.52 is away 0.48
    sh = wf.orient([_row(START - 1000, "lead", "T1", "BUY", 0.50),
                    _row(START - 900, "x", "T0", "BUY", 0.52)], g)
    assert wf.maker_fill(sh, 0, 0.49, -1, START)


def test_maker_follow_rests_a_tick_lower_when_the_ask_is_still_there():
    g = _game(home_won=True)
    rows = [_row(START - 1000, "smart", "T0", "BUY", 0.50),
            _row(START - 995, "x", "T0", "BUY", 0.50),      # ask still at 0.50
            _row(START - 900, "x", "T0", "SELL", 0.485),    # through 0.49
            _row(START - 10, "x", "T0", "BUY", 0.53)]       # close
    tapes = {"c1": wf.orient(rows, g)}
    m = wf.maker_follow([g], tapes, {"smart"}, window_s=600)
    assert m["signals"] == 1 and m["rests_at_signal_price"] == 0 and m["filled"] == 1
    assert m["filled_clv"]["mean"] == pytest.approx(0.53 - 0.49)
    # without the through print, the one-tick-lower bid never fills
    tapes = {"c1": wf.orient([rows[0], rows[1], rows[3]], g)}
    assert wf.maker_follow([g], tapes, {"smart"})["filled"] == 0
