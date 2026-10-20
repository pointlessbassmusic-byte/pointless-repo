"""ESPN sportsbook-close vs Polymarket study: odds maths, orientation, the bar."""
import pytest

from sportsbot.backtest import espn_line_study as els


def test_american_odds_and_proportional_devig():
    assert els.american_to_prob(-185) == pytest.approx(185 / 285)
    assert els.american_to_prob("+154") == pytest.approx(100 / 254)
    assert els.american_to_prob("x") is None and els.american_to_prob(0) is None
    p = els.devig(185 / 285, 100 / 254)
    assert p == pytest.approx((185 / 285) / (185 / 285 + 100 / 254))
    assert els.devig(None, 0.5) is None


def test_book_close_reads_the_first_provider_with_both_closes():
    odds = {"items": [
        {"provider": {"name": "Empty"}, "homeTeamOdds": {}, "awayTeamOdds": {}},
        {"provider": {"name": "DraftKings"},
         "homeTeamOdds": {"moneyLine": -185, "open": {"american": "-155"}, "close": {"american": "-185"}},
         "awayTeamOdds": {"moneyLine": 154, "open": {"american": "+130"}, "close": {"american": "+154"}}}]}
    bk = els.book_close(odds)
    assert bk["provider"] == "DraftKings" and 0.62 < bk["close"] < 0.63 and 0.58 < bk["open"] < 0.59
    assert els.book_close({"items": []}) is None


def test_last_print_takes_the_latest_in_window_and_refuses_rails():
    hist = [{"t": 100, "p": 0.40}, {"t": 160, "p": 0.42}, {"t": 300, "p": 0.99}]
    assert els.last_print(hist, 50, 200) == 0.42
    assert els.last_print(hist, 200, 400) is None        # the only print is at the rail
    assert els.last_print(hist, 400, 500) is None


def _row(book, pm_start, pm_t15, won):
    return {"token": f"t{book}{pm_t15}{won}", "book": book, "pm_start": pm_start, "pm_t15": pm_t15,
            "home_won": won}


def test_trade_buys_toward_the_book_on_either_side_net_of_costs():
    rows = [_row(0.60, 0.55, 0.55, True),     # book 5c above PM on home: buy home at 0.555, wins
            _row(0.30, 0.36, 0.36, True),     # book 6c below on home = above on away: buy away at 0.645, loses
            _row(0.50, 0.50, 0.505, False)]   # gap under the margin: no trade
    a = els.analyse(rows, fee_rate=0.05)
    assert a["trades"] == 2
    win = 1 - 0.555 - 0.05 * 0.555 * 0.445
    lose = 0 - 0.645 - 0.05 * 0.645 * 0.355
    assert a["trade_mean"] == pytest.approx((win + lose) / 2)
    assert a["rows_a"] == 3 and "beta" in a


def test_the_bar_needs_both_a_better_brier_and_a_paid_trade():
    good = {"diff": -0.01, "diff_se": 0.004, "trades": 150, "trade_mean": 0.02, "trade_se": 0.008}
    assert els.verdict(good).startswith("LEADS")
    assert els.verdict(dict(good, trades=99)) == "not a member"
    assert els.verdict(dict(good, diff_se=0.006)) == "not a member"
    assert els.verdict(dict(good, trade_mean=-0.01)) == "not a member"
