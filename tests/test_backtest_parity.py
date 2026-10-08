"""Backtest/live parity: the Kalshi backtests decide through the live
strategy (`live_policy_bet` -> bot.strategy.evaluate_market_verbose)."""
from datetime import datetime, timezone

import pytest

from sportsbot.backtest.kalshi_market import live_policy_bet
from sportsbot.core.types import Prediction, Sport

T = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _pred(p, unc=0.02):
    return Prediction(market_id="KXMLBGAME-X", sport=Sport.BASEBALL, model="t",
                      prob_yes=p, uncertainty=unc)


def _bet(p_model, bid, ask, fill_model="pessimistic", ticker="KXMLBGAME-26SEP01NYMATL-NYM",
         sport=Sport.BASEBALL, unc=0.02, won=True):
    return live_policy_bet(ticker=ticker, sport=sport, date=T, bid=bid, ask=ask, closing=0.5,
                           prediction=_pred(p_model, unc), home_won=won, fee_multiplier=0.5,
                           model_weight=0.3, slippage=0.005, min_edge=0.02, kelly=0.25,
                           bankroll=1000.0, max_stake=50.0, fill_model=fill_model)


def test_clear_edge_bets_and_pessimistic_pays_the_ask():
    b = _bet(0.80, 0.48, 0.50)          # blended q = 0.584 vs ask 0.50
    assert b is not None and b.side == "YES" and b.entry == 0.50
    assert b.pnl > 0 and b.stake <= 50.0


def test_optimistic_is_the_live_maker_one_tick_inside():
    b = _bet(0.80, 0.48, 0.50, fill_model="optimistic")
    assert b is not None and b.entry == 0.49


def test_live_filters_apply_that_the_legacy_rule_skipped():
    assert _bet(0.99, 0.90, 0.91) is None            # mid outside [0.15, 0.85]
    assert _bet(0.80, 0.40, 0.50) is None            # spread 0.10 > 0.03
    assert _bet(0.80, 0.48, 0.50, unc=0.5) is None   # model too uncertain


def test_maker_fee_free_series_in_optimistic_mode():
    paid = _bet(0.80, 0.48, 0.50, fill_model="optimistic")
    free = _bet(0.80, 0.48, 0.50, fill_model="optimistic",
                ticker="KXITFMATCH-26SEP01AB-A", sport=Sport.TENNIS)
    assert free is not None and paid is not None
    per_paid = (paid.stake / paid.entry - paid.stake - paid.pnl)
    per_free = (free.stake / free.entry - free.stake - free.pnl)
    assert per_free == pytest.approx(0.0, abs=0.01) and per_paid > 0


def test_strict_fill_model_is_refused_without_a_tape():
    with pytest.raises(ValueError):
        _bet(0.80, 0.48, 0.50, fill_model="strict")
