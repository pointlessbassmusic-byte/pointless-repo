"""Kalshi hourly crypto study: stamps, model, strike selection, scoring."""
import math

import pytest

from src import crypto_hourly_study as cs


def test_spot_candles_are_keyed_by_close_not_open():
    # Coinbase rows: [open_time, low, high, open, close, volume]
    s = cs.SpotSeries([[1000, 0, 0, 0, 50.0, 1], [940, 0, 0, 0, 49.0, 1]])
    assert s.at(1059) == 49.0          # the 1000-1060 bar has not closed yet
    assert s.at(1060) == 50.0
    assert s.at(1060 + 400) is None    # stale beyond three minutes


def test_model_probability_shape():
    assert cs.model_prob(100.0, 100.0, 0.001, 10) == pytest.approx(0.5)
    assert cs.model_prob(101.0, 100.0, 0.001, 10) > 0.9
    assert cs.model_prob(100.0, 100.0, 0.001, 0.5) is None   # inside the averaging minute
    # less time left -> more certain
    assert cs.model_prob(100.2, 100.0, 0.001, 2) > cs.model_prob(100.2, 100.0, 0.001, 30)


def test_strikes_are_chosen_from_spot_never_from_result_or_volume():
    ms = [{"strike_type": "greater", "floor_strike": k, "result": r, "volume_fp": v}
          for k, r, v in ((100.0, "yes", "1"), (101.0, "no", "999999"), (120.0, "no", "5"),
                          (99.5, None, "1"))]
    got = cs.select_strikes(ms, spot0=100.2, band=0.015)
    assert [m["floor_strike"] for m in got] == [100.0, 101.0]   # 120 too far; unsettled skipped


def test_quote_uses_the_candle_that_ended_by_then():
    candles = [{"end_period_ts": 60, "yes_bid": {"close_dollars": "0.40"}, "yes_ask": {"close_dollars": "0.42"}},
               {"end_period_ts": 120, "yes_bid": {"close_dollars": "0.60"}, "yes_ask": {"close_dollars": "0.62"}}]
    assert cs._quote(candles, 119) == (0.40, 0.42)
    assert cs._quote(candles, 120) == (0.60, 0.62)
    assert cs._quote(candles, 59) == (None, None)


def _dump(book_bid, book_ask, result, level=101.0):
    close = 7200.0
    # flat spot at `level` with a little noise so sigma > 0
    spot = [[t, 0, 0, 0, level + (0.05 if (t // 60) % 2 else -0.05), 1] for t in range(0, 7200, 60)]
    candles = [{"end_period_ts": t, "yes_bid": {"close_dollars": str(book_bid)},
                "yes_ask": {"close_dollars": str(book_ask)}} for t in range(int(close) - 3600, int(close) + 1, 60)]
    return {"series": "KXBTCD", "spot": spot,
            "events": [{"event": "E1", "close": close,
                        "markets": [{"ticker": "T", "strike": 100.0, "result": result, "candles": candles}]}]}


def test_taker_rule_fires_when_the_book_lags_and_is_paid_at_settlement():
    a = cs.analyse(_dump(0.50, 0.52, "yes"), margins=(0.0,), taus=(10,))
    r = a["by_tau"][10]
    t = r["taker"][0.0]
    assert t["n"] == 1                                   # model ~1.0 vs ask 0.52: buy YES
    assert t["mean"] == pytest.approx(1 - 0.52 - cs.taker_fee(0.52))
    assert r["n"] == 1 and r["brier_model"] < r["brier_book"]


def test_no_trade_when_the_book_already_agrees():
    # spot at the strike: model ~0.5, book 0.49 / 0.51, the fee eats any gap
    # the bar closing at the decision minute prints level + 0.05 = 100.0, the strike
    a = cs.analyse(_dump(0.49, 0.51, "yes", level=99.95), margins=(0.0,), taus=(10,))
    assert a["by_tau"][10]["taker"][0.0]["n"] == 0


def test_fee_is_kalshis_quadratic():
    assert cs.taker_fee(0.5) == pytest.approx(0.0175)
    assert math.isclose(cs.taker_fee(0.9), 0.07 * 0.09)
