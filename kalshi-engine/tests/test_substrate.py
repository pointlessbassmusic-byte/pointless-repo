from datetime import datetime, timedelta, timezone

from src.client import Market
from src.strategy.edge import build_signals, kelly_stake
from src.substrate.base import Context
from src.substrate.ensemble import Ensemble
from src.substrate.generators.market_implied import MarketImplied
from src.substrate.generators.mean_reversion import MeanReversion


def make_market(mid=0.60, ticker="TEST-24"):
    return Market(
        ticker=ticker, event_ticker="TEST", title="Test market",
        yes_bid=mid - 0.01, yes_ask=mid + 0.01, last_price=mid,
        volume=1000, open_interest=500,
        expiration=datetime.now(timezone.utc) + timedelta(days=5), status="open",
    )


def test_market_implied_anchors_to_mid():
    gen = MarketImplied({"confidence": 0.5})
    f = gen.forecast(make_market(0.60), Context())
    assert f is not None
    assert abs(f.prob_yes - 0.60) < 1e-9


def test_mean_reversion_fades_big_move():
    gen = MeanReversion({"confidence": 0.3, "lookback_scans": 3,
                         "overreaction_threshold": 0.10, "reversion_factor": 0.5})
    m = make_market(0.70)
    ctx = Context(price_history={m.ticker: [("t1", 0.50), ("t2", 0.55), ("t3", 0.65)]})
    f = gen.forecast(m, ctx)
    assert f is not None
    assert f.prob_yes < 0.70  # fades back toward 0.50


def test_ensemble_weighted_average():
    ens = Ensemble([MarketImplied({"confidence": 0.5})])
    res = ens.predict(make_market(0.40), Context())
    assert res is not None
    assert abs(res.prob_yes - 0.40) < 1e-9


def test_kelly_and_signals():
    assert kelly_stake(0.5, 0.6, 1000, 0.25) == 0.0
    ens = Ensemble([MarketImplied({"confidence": 0.5})])
    res = ens.predict(make_market(0.5), Context())
    # fair == price → no signals
    assert build_signals([res], {"min_edge": 0.05}) == []


def test_signals_skip_already_ordered_and_respect_open_exposure():
    from src.substrate.ensemble import EnsembleResult

    m = make_market(0.60)
    m.yes_bid, m.yes_ask = 0.59, 0.61
    res = EnsembleResult(market=m, prob_yes=0.75, total_confidence=1.0, forecasts=[])

    assert build_signals([res], {}) != []
    assert build_signals([res], {}, exclude_tickers={m.ticker}) == []
    # default max_total_exposure=200; nearly all of it already committed
    assert build_signals([res], {}, existing_exposure=199.0) == []
