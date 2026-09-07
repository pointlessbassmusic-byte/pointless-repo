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


def test_read_prod_routes_public_reads_to_prod():
    from src.client import DEMO_BASE, PROD_BASE, KalshiClient

    c = KalshiClient(demo=True, read_prod=True)
    assert c.read_base == PROD_BASE and c.trade_base == DEMO_BASE
    c2 = KalshiClient(demo=True, read_prod=False)
    assert c2.read_base == DEMO_BASE


def test_wide_spread_is_filtered():
    from src.substrate.ensemble import EnsembleResult

    m = make_market(0.60)
    m.yes_bid, m.yes_ask = 0.45, 0.75  # 30-cent book
    res = EnsembleResult(market=m, prob_yes=0.95, total_confidence=1.0, forecasts=[])
    assert build_signals([res], {"max_spread": 0.10}) == []
    assert build_signals([res], {"max_spread": 0.50}) != []


def test_settlements_roundtrip(tmp_path):
    from src.storage.db import Database

    db = Database(tmp_path / "t.db")
    db.record_forecasts("TICK-A", [])
    db.conn.execute(
        "INSERT INTO forecasts (ts, ticker, generator, prob_yes, confidence, rationale)"
        " VALUES ('2026-01-01', 'TICK-A', 'g', 0.7, 0.5, '')"
    )
    db.conn.commit()
    assert db.unsettled_forecast_tickers() == ["TICK-A"]
    db.record_settlements({"TICK-A": "yes"})
    assert db.settled_outcomes() == {"TICK-A": 1.0}
    assert db.unsettled_forecast_tickers() == []


def test_parse_market_handles_dollar_and_legacy_cent_fields():
    from src.client import _parse_market

    new_style = _parse_market({
        "ticker": "T-NEW", "title": "t", "status": "active",
        "yes_bid_dollars": "0.6200", "yes_ask_dollars": "0.6400",
        "last_price_dollars": "0.6300", "volume_fp": "1663.42",
        "open_interest_fp": "5160.60", "close_time": "2026-09-07T05:00:00Z",
    })
    assert abs(new_style.yes_bid - 0.62) < 1e-9
    assert abs(new_style.mid - 0.63) < 1e-9
    assert new_style.volume == 1663

    legacy = _parse_market({
        "ticker": "T-OLD", "title": "t", "status": "open",
        "yes_bid": 62, "yes_ask": 64, "last_price": 63, "volume": 100,
    })
    assert abs(legacy.yes_bid - 0.62) < 1e-9
    assert legacy.volume == 100


def test_risk_gate_kill_switch_and_daily_loss(tmp_path):
    from src.risk import RiskGate
    from src.storage.db import Database

    db = Database(tmp_path / "t.db")
    gate = RiskGate(db, {"max_daily_loss_usd": 10, "kill_switch_file": "KILL"}, tmp_path)
    assert gate.check() == (True, "")

    # a losing settled trade today: bought 200 yes @ 0.40, market resolved no
    db.conn.execute(
        "INSERT INTO orders (ts, ticker, side, price, count, stake_usd, status)"
        " VALUES (datetime('now'), 'T1', 'yes', 0.40, 200, 80, 'placed:abc')"
    )
    db.conn.commit()
    db.record_settlements({"T1": "no"})
    assert db.realized_pnl_today() == -80.0
    ok, reason = gate.check()
    assert not ok and "loss limit" in reason

    (tmp_path / "KILL").touch()
    ok, reason = gate.check()
    assert not ok and "kill switch" in reason


def test_momentum_follows_steady_drift_only():
    from src.substrate.generators.momentum import Momentum

    gen = Momentum({"confidence": 0.25, "lookback_scans": 6, "min_total_move": 0.03,
                    "max_step": 0.05, "min_consistency": 0.7, "continuation_factor": 0.4})
    m = make_market(0.56)
    steady = [(f"t{i}", 0.50 + 0.01 * i) for i in range(6)]  # 0.50 → 0.55
    f = gen.forecast(m, Context(price_history={m.ticker: steady}))
    assert f is not None and f.prob_yes > 0.56

    # one 15-cent jump is a news shock, not drift
    shock = [("t0", 0.40), ("t1", 0.40), ("t2", 0.55), ("t3", 0.55), ("t4", 0.55), ("t5", 0.55)]
    assert gen.forecast(m, Context(price_history={m.ticker: shock})) is None

    # choppy back-and-forth has no direction to follow
    chop = [("t0", 0.52), ("t1", 0.56), ("t2", 0.52), ("t3", 0.56), ("t4", 0.52), ("t5", 0.56)]
    assert gen.forecast(m, Context(price_history={m.ticker: chop})) is None


def test_backtest_replay_scores_against_outcome(tmp_path):
    from src.backtest import replay
    from src.storage.db import Database
    from src.substrate.ensemble import Ensemble

    db = Database(tmp_path / "t.db")
    # a market drifting to a YES resolution, one scan at a time
    for i, mid in enumerate([0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.97]):
        db.conn.execute("INSERT INTO prices (ts, ticker, mid) VALUES (?,?,?)",
                        (f"2026-01-0{i + 1}T00:00:00+00:00", "TICK", mid))
    db.conn.commit()

    ens = Ensemble([MarketImplied({"confidence": 0.5})])
    scores = replay(db, ens, min_scans=3)
    assert scores["ensemble"].n > 0
    assert scores["market (baseline)"].n == scores["ensemble"].n
    # market-implied == baseline here, so their Brier must match
    assert abs(scores["ensemble"].brier - scores["market (baseline)"].brier) < 1e-9
