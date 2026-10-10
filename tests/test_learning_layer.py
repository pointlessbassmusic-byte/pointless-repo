"""The pieces around the portfolio: style/budget enforcement in the
strategy, fractional risk limits, arm tagging on fills, the sharp-line
model, and the runner's per-cycle wiring. Every case is a way real money
could leak past the allocation."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sportsbot.bot.risk import RiskConfig, RiskManager
from sportsbot.bot.strategy import StrategyConfig, evaluate_market_verbose
from sportsbot.core.staking import StakingConfig, decide_stake
from sportsbot.core.types import (
    BookLevel,
    Exchange,
    MarketInfo,
    MarketQuote,
    Prediction,
    Sport,
)
from sportsbot.data.store import Store


def _market(mid="M1", sport=Sport.BASEBALL):
    return MarketInfo(exchange=Exchange.POLYMARKET, market_id=mid, slug=mid, sport=sport,
                      home="A", away="B",
                      start_time=datetime.now(timezone.utc) + timedelta(hours=5))


def _quote(bid, ask, size=1000.0):
    return MarketQuote(market_id="M1", bid=bid, ask=ask,
                       bids=[BookLevel(price=bid, size=size)],
                       asks=[BookLevel(price=ask, size=size)])


def _pred(p=0.70):
    return Prediction(market_id="M1", sport=Sport.BASEBALL, model="m", prob_yes=p)


STAKING = StakingConfig(bankroll=1000.0, min_stake=5.0, max_stake_per_market=50.0)


def FEE(p, s, m=None):
    return 0.0


# ---------------------------------------------------------------------------
# sizing: the sport budget only tightens
# ---------------------------------------------------------------------------
def test_sport_budget_tightens_the_fraction_cap_and_never_widens_it():
    base = decide_stake(0.70, 0.50, STAKING)                       # 20% cap = $200 room
    tight = decide_stake(0.70, 0.50, STAKING, sport_budget=12.0)
    assert tight.approved and tight.stake == 12.0 and "sport cap" in " ".join(tight.reasons)
    loose = decide_stake(0.70, 0.50, STAKING, sport_budget=10_000.0)
    assert loose.stake == base.stake
    assert not decide_stake(0.70, 0.50, STAKING, sport_budget=0.0).approved


def test_strategy_applies_style_and_budget_from_the_allocation():
    m, q, p = _market(), _quote(0.49, 0.52), _pred()   # 3-tick book; 0.52-0.49 is 0.03 plus float noise, so the limit is widened
    both = evaluate_market_verbose(m, q, p, STAKING, StrategyConfig(max_spread=0.05), FEE, {})
    assert both[0] is not None and both[0].reason.endswith("maker")
    # taker-only arm: must cross, never post inside
    taker = evaluate_market_verbose(m, q, p, STAKING, StrategyConfig(max_spread=0.05,
        style_override={"baseball": "taker"}), FEE, {})
    assert taker[0] is not None and taker[0].reason.endswith("taker")
    assert taker[0].price == pytest.approx(0.52)
    # maker-only arm on a one-tick book: skip, do not take
    one_tick = evaluate_market_verbose(m, _quote(0.49, 0.50), p, STAKING, StrategyConfig(max_spread=0.05,
        style_override={"baseball": "maker"}, post_inside_spread=False), FEE, {})
    assert one_tick[0] is None and "maker-only" in one_tick[1]
    # unfunded sport: nothing, with the reason
    none = evaluate_market_verbose(m, q, p, STAKING, StrategyConfig(max_spread=0.05,
        style_override={"baseball": "none"}), FEE, {})
    assert none[0] is None and "no funded arm" in none[1]
    # budget caps the stake
    capped = evaluate_market_verbose(m, q, p, STAKING, StrategyConfig(max_spread=0.05,
        sport_budget={"baseball": 7.0}), FEE, {})
    assert capped[0] is not None and capped[0].price * capped[0].size <= 7.0 + 1e-6


# ---------------------------------------------------------------------------
# risk: fractions of the live bankroll, tighter wins
# ---------------------------------------------------------------------------
def test_fractional_risk_limits_follow_the_bankroll_and_take_the_tighter_value(tmp_path):
    cfg = RiskConfig(daily_loss_limit=100.0, max_drawdown=250.0,
                     daily_loss_fraction=0.10, max_drawdown_fraction=0.25, bankroll=1000.0)
    assert cfg.effective_daily_loss() == 100.0 and cfg.effective_max_drawdown() == 250.0
    mgr = RiskManager(cfg, Store(str(tmp_path / "r.sqlite")))
    mgr.set_bankroll(100.0)
    assert cfg.effective_daily_loss() == pytest.approx(10.0)
    assert cfg.effective_max_drawdown() == pytest.approx(25.0)
    mgr.set_bankroll(0.0)                                  # never below the last positive figure
    assert cfg.bankroll == 100.0
    plain = RiskConfig(daily_loss_limit=100.0, bankroll=10.0)
    assert plain.effective_daily_loss() == 100.0           # no fraction set: the dollar limit stands


def test_daily_loss_fraction_stops_a_small_account(tmp_path):
    store = Store(str(tmp_path / "r.sqlite"))
    mgr = RiskManager(RiskConfig(daily_loss_limit=100.0, daily_loss_fraction=0.10,
                                 bankroll=100.0), store)
    bid = store.record_bet("M1", "baseball", "yes", 0.6, 0.5, 20.0, 40, 0.05, "paper", "paper")
    store.settle_bet(bid, outcome=0, pnl=-12.0)
    ok, reason = mgr.check_global()
    assert not ok and "daily loss" in reason


# ---------------------------------------------------------------------------
# fills carry their arm
# ---------------------------------------------------------------------------
def test_paper_fills_are_tagged_with_their_arm(tmp_path):
    from sportsbot.bot.executor import Executor
    from sportsbot.core.types import BetIntent, Side
    from sportsbot.exchanges.paper import PaperExchange

    store = Store(str(tmp_path / "e.sqlite"))
    paper = PaperExchange(data_client=None, starting_balance=100.0, fee_fn=FEE)
    ex = Executor(paper, store, mode="paper")
    intent = BetIntent(market=_market(), side=Side.YES, prob=0.7, price=0.52, size=10,
                       edge=0.1, kelly_fraction=0.01,
                       reason="sharpline p=0.700 blend=0.650 mid=0.500 taker")
    ex.submit(intent, quote=_quote(0.48, 0.52))
    rows = store.all_bets("paper")
    assert len(rows) == 1 and rows[0]["arm"] == "baseball/sharp/taker"


# ---------------------------------------------------------------------------
# the sharp line as a model
# ---------------------------------------------------------------------------
def _seed_sharp(store, when, p_home=0.62):
    store.record_sharp_quotes([{
        "sport_key": "baseball_mlb", "event_id": "ev1",
        "commence_time": (when + timedelta(hours=5)).isoformat(),
        "home_team": "Los Angeles Dodgers", "away_team": "San Francisco Giants",
        "bookmaker": "pinnacle", "home_implied": p_home + 0.02, "away_implied": 1 - p_home + 0.02,
        "home_fair": p_home, "away_fair": 1 - p_home, "overround": 0.04}],
        ts=when.isoformat())


def test_sharp_line_model_prices_from_the_store_and_refuses_stale_or_unknown(tmp_path):
    from sportsbot.engine.base import EventInput
    from sportsbot.engine.sharpline import SharpLineModel

    store = Store(str(tmp_path / "s.sqlite"))
    now = datetime.now(timezone.utc)
    _seed_sharp(store, now - timedelta(minutes=10))
    model = SharpLineModel(store, Sport.BASEBALL, max_age_minutes=60)
    assert set(model.rated_entities()) == {"Los Angeles Dodgers", "San Francisco Giants"}
    # venue lists the visitor first: YES side is the book's away team
    pred = model.predict(EventInput(sport=Sport.BASEBALL, home="San Francisco Giants",
                                    away="Los Angeles Dodgers",
                                    start_time=now + timedelta(hours=5)))
    assert pred.prob_yes == pytest.approx(0.38) and pred.uncertainty == 0.0
    assert pred.model == "sharpline"
    unknown = model.predict(EventInput(sport=Sport.BASEBALL, home="X", away="Y"))
    assert unknown.uncertainty == 1.0
    stale = SharpLineModel(store, Sport.BASEBALL, max_age_minutes=5)
    assert stale.predict(EventInput(sport=Sport.BASEBALL, home="Los Angeles Dodgers",
                                    away="San Francisco Giants")).uncertainty == 1.0
    # the strategy skips an uncertain prediction with the reason
    out = evaluate_market_verbose(_market(), _quote(0.48, 0.52), unknown, STAKING,
                                  StrategyConfig(), FEE, {})
    assert out[0] is None and "uncertainty" in out[1]


def test_scanner_uses_the_sharp_models_entities_and_load_models_switches_on_signal(tmp_path):
    from sportsbot.bot.runner import load_models
    from sportsbot.bot.scanner import Scanner
    from sportsbot.engine.sharpline import SharpLineModel

    store = Store(str(tmp_path / "s.sqlite"))
    _seed_sharp(store, datetime.now(timezone.utc))
    cfg = {"sports": {"baseball": {"enabled": True, "signal": "sharp"},
                      "tennis": {"enabled": False}, "table_tennis": {"enabled": False}}}
    models = load_models(cfg, str(tmp_path), store=store)
    assert isinstance(models[Sport.BASEBALL], SharpLineModel)
    sc = Scanner(models)
    m = MarketInfo(exchange=Exchange.POLYMARKET, market_id="c1", slug="mlb-sf-lad", sport=Sport.BASEBALL,
                   home="San Francisco Giants", away="Los Angeles Dodgers",
                   start_time=datetime.now(timezone.utc) + timedelta(hours=5),
                   meta={"home_field": "Los Angeles Dodgers"})
    scanned, drops = sc.scan_verbose([m])
    assert len(scanned) == 1 and drops == []
    assert scanned[0].prediction.prob_yes == pytest.approx(0.38)
    with pytest.raises(ValueError):
        load_models(cfg, str(tmp_path))                     # sharp signal needs the store
    plain = load_models({"sports": {"baseball": {"enabled": True}, "tennis": {"enabled": False},
                                    "table_tennis": {"enabled": False}}}, str(tmp_path))
    assert not isinstance(plain[Sport.BASEBALL], SharpLineModel)


# ---------------------------------------------------------------------------
# runner wiring, per cycle
# ---------------------------------------------------------------------------
def test_cycle_configs_apply_equity_bankroll_and_the_portfolio(tmp_path, monkeypatch):
    from sportsbot.bot.portfolio import AllocationConfig
    from sportsbot.bot.runner import Runner

    store = Store(str(tmp_path / "r.sqlite"))
    # paper equity: $100 start, +$40 realised
    bid = store.record_bet("M1", "baseball", "yes", 0.6, 0.5, 20.0, 40, 0.05, "paper", "paper")
    store.settle_bet(bid, outcome=1, pnl=40.0, closing_price=0.55)
    cfg = {"bankroll": {"amount": 100.0, "max_total_exposure": 0.5, "max_fraction_per_sport": 0.2},
           "sports": {"baseball": {"enabled": True}, "tennis": {"enabled": False},
                      "table_tennis": {"enabled": False}},
           "execution": {"post_inside_spread": True}, "adaptive": {"enabled": False}}
    stub = SimpleNamespace(
        cfg=cfg, store=store, account="sim", mode="paper", starting_balance=100.0,
        staking=StakingConfig(bankroll=100.0), strategy=StrategyConfig(),
        adaptive={"enabled": False, "drawdown_stake_scaling": False},
        alloc_cfg=AllocationConfig(bankroll_mode="equity"),
        running_arms={"baseball/model/maker", "baseball/model/taker"},
        risk=RiskManager(RiskConfig(bankroll=100.0), store),
        sharp_cfg=SimpleNamespace(enabled=False, enforce_adaptive=False),
        _priceable_sports=lambda: {"baseball"},
        _cycle_configs=Runner._cycle_configs)
    staking, strategy = stub._cycle_configs(stub)
    assert staking.bankroll == pytest.approx(140.0)             # profits rolled in
    assert stub.risk.cfg.bankroll == pytest.approx(140.0)
    assert strategy.sport_budget == {"baseball": pytest.approx(28.0)}   # 20% of 140
    assert strategy.style_override == {"baseball": "both"}
    last = store.get_kv("portfolio:last")
    assert last["bankroll"] == 140.0 and last["rows"][0]["arm"].startswith("baseball/model")
    # config mode: the config amount stands
    stub.alloc_cfg = AllocationConfig(bankroll_mode="config")
    staking, _ = stub._cycle_configs(stub)
    assert staking.bankroll == 100.0


def test_cycle_configs_fail_closed_when_allocation_raises(tmp_path, monkeypatch):
    from sportsbot.bot import runner as runner_mod
    from sportsbot.bot.portfolio import AllocationConfig

    store = Store(str(tmp_path / "r.sqlite"))
    stub = SimpleNamespace(
        cfg={"sports": {"baseball": {"enabled": True}}}, store=store, account="sim",
        mode="paper", starting_balance=100.0, staking=StakingConfig(bankroll=100.0),
        strategy=StrategyConfig(), adaptive={"enabled": False, "drawdown_stake_scaling": False},
        alloc_cfg=AllocationConfig(), running_arms=set(),
        risk=RiskManager(RiskConfig(bankroll=100.0), store),
        sharp_cfg=SimpleNamespace(enabled=False, enforce_adaptive=False),
        _priceable_sports=lambda: set(),
        _cycle_configs=runner_mod.Runner._cycle_configs)
    monkeypatch.setattr(runner_mod, "allocate_arms",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    _, strategy = stub._cycle_configs(stub)
    assert set(strategy.style_override.values()) == {"none"}


def test_a_one_tick_book_is_one_tick_despite_float_noise():
    """0.50 - 0.49 is 0.010000000000000009; without a tolerance the strategy
    'posted inside' at bid + tick == the ask, a taker fill labelled maker."""
    out = evaluate_market_verbose(_market(), _quote(0.49, 0.50), _pred(), STAKING,
                                  StrategyConfig(max_spread=0.05), FEE, {})
    assert out[0] is not None and out[0].reason.endswith("taker")
    assert out[0].price == pytest.approx(0.50)
