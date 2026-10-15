"""Kalshi hourly index study: bar stamps, sigma, model, strike selection, scoring, the bar."""
import math

import pytest

from src import index_hourly_study as ix

H = 3600
OPEN = 13 * H + 1800          # sessions open on the half hour (13:30Z)


def _session(day: int, base: float = 100.0, step: float = 0.1):
    """Seven Yahoo-style bars: six hourly, a 15:30 half hour, and the zero-length closing print."""
    t0 = day * 86400 + OPEN
    ts = [t0 + i * H for i in range(7)] + [t0 + 6 * H + 1800]
    closes = [base + step * ((i % 2) * 2 - 1) for i in range(7)] + [base + 5.0]
    opens = [base] * 8
    return ts, opens, closes


def test_bars_are_keyed_by_close_and_the_closing_print_is_dropped():
    ts, o, c = _session(0)
    b = ix.IndexBars(ts, o, c)
    first = ts[0]
    assert b.at(first + H - 1) is None                 # the 13:30 bar closes at 14:30
    assert b.at(first + H) == c[0]
    last_half = ts[6]                                  # 19:30Z bar ends at the 20:00Z closing stamp
    assert b.at(last_half + 1800) == c[6]
    assert b.at(last_half + 1800 + 60) == c[6]         # the closing print (base + 5) never appears
    assert c[7] not in b.close


def test_sigma_ignores_the_overnight_gap():
    ts1, o1, c1 = _session(0)
    ts2, o2, c2 = _session(1, base=200.0)              # a 100% overnight gap
    b = ix.IndexBars(ts1 + ts2, o1 + o2, c1 + c2)
    s = b.sigma(ts2[-2] + 1800, lookback=10)
    assert s is not None and s < 0.01                  # the gap would make it ~0.08 per minute


def test_model_probability_is_a_point_settlement():
    assert ix.model_prob(100.0, 100.0, 0.001, 30) == pytest.approx(0.5)
    assert ix.model_prob(101.0, 100.0, 0.0005, 30) > 0.99
    assert ix.model_prob(100.0, 100.0, 0.001, 0) is None
    assert ix.vix_sigma(20.0) == pytest.approx(0.2 / math.sqrt(252 * 390))


def test_strikes_from_the_index_at_decision_never_from_result_or_volume():
    ms = [{"strike_type": "greater_or_equal", "floor_strike": k, "result": r, "volume_fp": v}
          for k, r, v in ((7700, "yes", "1"), (7725, "no", "99999"), (7900, "no", "1"), (7705, None, "1"))]
    got = ix.select_strikes(ms, spot=7710.0, band=0.006)
    assert [m["floor_strike"] for m in got] == [7700, 7725]


def test_previous_vix_close_is_from_an_earlier_session():
    v = ix.DailyCloses([0, 86400], [15.0, 30.0])
    assert v.previous(86400 + 5 * H) == 15.0           # today's close is not known intraday


def _dump(bid, ask, result, strike=100.0):
    ts, o, c = [], [], []
    for d in range(6):
        a, b_, cc = _session(d)
        ts += a
        o += b_
        c += cc
    close = 5 * 86400 + 20 * H                         # 16:00 ET settlement on day 5
    candles = [{"end_period_ts": t, "yes_bid": {"close_dollars": str(bid)}, "yes_ask": {"close_dollars": str(ask)}}
               for t in range(close - 6000, close + 1, 60)]
    return {"series": "KXINXU", "bars": {"t": ts, "open": o, "close": c},
            "vix": {"t": [d * 86400 for d in range(6)], "close": [15.0] * 6},
            "events": [{"event": "KXINXU-X", "close": close,
                        "markets": [{"ticker": "T", "strike": strike, "result": result, "candles": candles}]}]}


def test_taker_rule_is_paid_at_settlement_net_of_fee():
    rows = list(ix._rows([_dump(0.50, 0.52, "yes", strike=99.0)], 30))
    assert len(rows) == 1 and rows[0][2] > 0.99         # index ~100 vs strike 99: model near 1
    r = ix.score(rows, margins=(0.0,))
    assert r["taker"][0.0]["n"] == 1
    assert r["taker"][0.0]["mean"] == pytest.approx(1 - 0.52 - ix.taker_fee(0.52))


def test_the_bar_needs_both_brier_and_a_taker_profit():
    good = {"brier_diff": -0.01, "brier_diff_se": 0.004,
            "taker": {0.02: {"n": 150, "events": 60, "mean": 0.02, "se": 0.008}}}
    assert ix.verdict(good).startswith("MEMBER")
    few = dict(good, taker={0.02: {"n": 99, "events": 60, "mean": 0.02, "se": 0.008}})
    assert ix.verdict(few) == "not a member"
    weak = dict(good, brier_diff_se=0.006)
    assert ix.verdict(weak) == "not a member"


def test_historical_candles_read_like_live_ones():
    c = ix.normalise_candle({"end_period_ts": 60, "yes_bid": {"close": "0.4000"}, "yes_ask": {"close": "0.4200"}})
    assert ix._quote([c], 60) == (0.40, 0.42)


def test_fetch_adds_archived_events_once():
    class F(ix.IndexFetcher):
        def __init__(self):
            pass

        def yahoo(self, symbol, interval, rng):
            d = _dump(0.5, 0.52, "yes")
            return d["bars"] if interval == "60m" else d["vix"]

        def events(self, series, days, now):
            return [{"event_ticker": "LIVE", "markets": [self._m("LIVE")]}, {"event_ticker": "EMPTY", "markets": []}]

        def historical_events(self, series):
            return [{"event_ticker": "LIVE", "markets": [self._m("LIVE")]}, {"event_ticker": "OLD", "markets": [self._m("OLD")]}]

        def _m(self, ev):
            return {"ticker": ev + "-T", "event_ticker": ev, "close_time": "1970-01-06T20:00:00Z",
                    "strike_type": "greater_or_equal", "floor_strike": 100.0, "result": "yes"}

        def batch_candles(self, tickers, close, span_s=6000):
            return {t: [{"src": "live"}] for t in tickers}

        def historical_candles(self, ticker, close, span_s=6000):
            return [{"src": "hist"}]

    import json
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with open(ix.fetch("KXINXU", fetcher=F(), now=1.0, cache_dir=d)) as fh:
            dump = json.load(fh)
    got = {e["event"]: e["markets"][0]["candles"][0]["src"] for e in dump["events"]}
    assert got == {"LIVE": "live", "OLD": "hist"}
