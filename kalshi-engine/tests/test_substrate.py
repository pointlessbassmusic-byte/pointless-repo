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


def test_fees_gate_marginal_edges():
    from src.substrate.ensemble import EnsembleResult

    m = make_market(0.50)
    m.yes_bid, m.yes_ask = 0.49, 0.50
    # raw edge exactly 0.05 at price 0.50 — fees (~0.0175) push it below min_edge
    res = EnsembleResult(market=m, prob_yes=0.55, total_confidence=1.0, forecasts=[])
    assert build_signals([res], {"min_edge": 0.05}) == []
    assert build_signals([res], {"min_edge": 0.05, "fee_rate": 0.0}) != []


def test_settled_positions_release_exposure(tmp_path):
    from src.storage.db import Database
    db = Database(tmp_path / "t.db")
    db.conn.execute(
        "INSERT INTO orders (ts, ticker, side, price, count, stake_usd, status)"
        " VALUES (datetime('now'), 'T1', 'yes', 0.40, 100, 40, 'placed:x')")
    db.conn.commit()
    assert db.live_exposure() == 40.0
    db.record_settlements({"T1": "yes"})
    assert db.live_exposure() == 0.0


def test_history_gap_disables_lookback_generators():
    from src.substrate.generators.momentum import Momentum

    gen = MeanReversion({"confidence": 0.3, "lookback_scans": 3,
                         "overreaction_threshold": 0.10, "reversion_factor": 0.5})
    m = make_market(0.70)
    # 3 rows spanning 3 DAYS with a 900s scan interval — a weekend gap
    gapped = [("2026-01-01T00:00:00+00:00", 0.50),
              ("2026-01-02T00:00:00+00:00", 0.55),
              ("2026-01-03T00:00:00+00:00", 0.65)]
    ctx = Context(price_history={m.ticker: gapped}, scan_interval_sec=900)
    assert gen.forecast(m, ctx) is None
    # same rows with no interval configured (backtest mode): check disabled
    ctx2 = Context(price_history={m.ticker: gapped})
    assert gen.forecast(m, ctx2) is not None

    mom = Momentum({"confidence": 0.25, "lookback_scans": 3, "min_total_move": 0.03,
                    "max_step": 0.06, "min_consistency": 0.7})
    assert mom.forecast(m, ctx) is None


def test_weather_band_probabilities():
    from src.substrate.generators.weather import band_probability

    # forecast dead-center in a 2-degree band -> dominant probability
    # 2-degree band under sigma=1.8: ~0.42 is the single most likely band
    p_band = band_probability(mu=77.5, sigma=1.8, floor=77, cap=78)
    assert 0.40 < p_band < 0.45
    # far-away band -> negligible
    assert band_probability(mu=77.5, sigma=1.8, floor=90, cap=91) < 0.01
    # threshold sides complement the middle
    p_below = band_probability(mu=77.5, sigma=1.8, floor=None, cap=77)   # <= 76
    p_above = band_probability(mu=77.5, sigma=1.8, floor=78, cap=None)   # >= 79
    assert abs(p_band + p_below + p_above - 1.0) < 1e-9


def test_weather_generator_end_to_end():
    from datetime import datetime, timezone
    from src.substrate.generators.weather import WeatherHigh, _event_date
    import time as _time

    today = datetime.now(timezone.utc).date()
    assert _event_date("KXHIGHNY-26SEP16-B77.5") is not None

    gen = WeatherHigh({"confidence": 0.4, "sigma_base_f": 1.8, "sigma_per_day_f": 0.6})
    ticker_date = today.strftime("%y%b%d").upper()
    m = make_market(0.48, ticker=f"KXHIGHNY-{ticker_date}-B77.5")
    m.event_ticker = f"KXHIGHNY-{ticker_date}"
    m.floor_strike, m.cap_strike = 77.0, 78.0
    # inject a cached forecast: no network in tests
    gen._cache["KXHIGHNY"] = (_time.monotonic(), {today.isoformat(): 77.5})
    f = gen.forecast(m, Context())
    assert f is not None and 0.40 < f.prob_yes < 0.45 and f.generator == "weather"

    # unknown station -> no view
    m2 = make_market(0.5, ticker=f"KXNOTACITY-{ticker_date}-B77.5")
    m2.event_ticker = f"KXNOTACITY-{ticker_date}"
    assert gen.forecast(m2, Context()) is None


def test_fit_sigma_recovers_linear_error_growth():
    from src.weather_calibrate import fit_sigma

    # symmetric errors whose spread grows with lead: std ~= 1.5 + 0.5*lead
    samples = []
    for lead in range(5):
        spread = 1.5 + 0.5 * lead
        samples += [(lead, spread), (lead, -spread)] * 5
    base, slope = fit_sigma(samples)
    assert 1.4 < base < 1.8 and 0.4 < slope < 0.7

    assert fit_sigma([(0, 1.0)]) is None  # too little data


def test_observed_from_settled_bands():
    from src.weather_calibrate import event_ticker_for, observed_from_markets
    from datetime import date

    def mk(result, floor, cap):
        m = make_market(0.5, ticker="KXHIGHNY-26SEP14-X")
        m.result, m.floor_strike, m.cap_strike = result, floor, cap
        return m

    ms = [mk("no", 76.0, 77.0), mk("yes", 74.0, 75.0), mk("no", None, 74.0)]
    assert observed_from_markets(ms) == 74.5
    # YES on an open-ended threshold only bounds the temp — censored, skip
    assert observed_from_markets([mk("yes", 81.0, None)]) is None
    assert event_ticker_for("KXHIGHNY", date(2026, 9, 14)) == "KXHIGHNY-26SEP14"


def test_weather_log_and_scoring_roundtrip(tmp_path):
    from src.storage.db import Database
    from src.weather_calibrate import self_logged_samples

    db = Database(tmp_path / "t.db")
    db.conn.execute("INSERT INTO weather_log (logged_date, station, target_date,"
                    " lead_days, forecast_f) VALUES ('2026-09-10','KXHIGHNY','2026-09-12',2,80.0)")
    db.conn.execute("INSERT INTO weather_observed (station, target_date, observed_f)"
                    " VALUES ('KXHIGHNY','2026-09-12',77.5)")
    db.conn.commit()
    assert self_logged_samples(db) == [(2, 2.5)]


def test_low_temperature_stations_use_min_variable():
    from src.substrate.generators.weather import DEFAULT_STATIONS, _daily_variable

    assert _daily_variable(DEFAULT_STATIONS["KXLOWTBOS"]) == "temperature_2m_min"
    assert _daily_variable(DEFAULT_STATIONS["KXHIGHTBOS"]) == "temperature_2m_max"
    assert _daily_variable({}) == "temperature_2m_max"  # default is the high
    # every station has full coordinates and the two variables never collide
    for name, st in DEFAULT_STATIONS.items():
        assert {"latitude", "longitude", "timezone"} <= set(st), name
        assert ("LOW" in name) == (st.get("variable") == "min"), name


def test_priority_prefixes_survive_the_liquidity_cut():
    """run_cycle's cap keeps station markets even at zero volume."""
    from src.main import GENERATOR_REGISTRY  # noqa: F401 — import sanity
    # replicate the cap logic directly on Market objects
    weather = make_market(0.5, ticker="KXHIGHNY-26SEP20-B80.5")
    weather.volume = 0
    whales = []
    for i in range(3):
        m = make_market(0.5, ticker=f"BIG-{i}")
        m.volume = 10_000 + i
        whales.append(m)
    markets, max_markets = whales + [weather], 2
    keep_prefixes = ("KXHIGH", "KXLOWT")
    priority = [m for m in markets if m.ticker.startswith(keep_prefixes)]
    rest = sorted((m for m in markets if m not in priority),
                  key=lambda m: m.volume, reverse=True)
    capped = priority + rest[:max(0, max_markets - len(priority))]
    assert weather in capped and len(capped) == 2
