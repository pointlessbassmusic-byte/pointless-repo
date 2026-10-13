"""Touch-maker arm: rests at the touch, one side per cycle, never
crosses, minimum size, through the normal risk and arm plumbing."""

from datetime import datetime, timedelta, timezone

import pytest

from sportsbot.bot.executor import Executor
from sportsbot.bot.portfolio import arm_of_intent, running_arms_for
from sportsbot.bot.touch import TouchConfig, evaluate_touch
from sportsbot.core.types import BookLevel, Exchange, MarketInfo, MarketQuote, Side, Sport
from sportsbot.data.store import Store
from sportsbot.exchanges.paper import PaperExchange

NOW = datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc)
NO_FEE = lambda price, market_id="": 0.0  # noqa: E731
EMPTY = {"total": 0.0, "by_sport": {}, "by_market": {}, "open_positions": 0}


def _market(**kw):
    d = dict(exchange=Exchange.POLYMARKET, market_id="m1", yes_token_id="t1", no_token_id="t2",
             slug="nyy-bos", sport=Sport.BASEBALL, home="New York Yankees", away="Boston Red Sox",
             start_time=NOW + timedelta(hours=3))
    d.update(kw)
    return MarketInfo(**d)


def _quote(bid=0.44, ask=0.47, depth=200.0):
    return MarketQuote(market_id="m1", bid=bid, ask=ask, ts=NOW,
                       bids=[BookLevel(price=bid, size=depth)],
                       asks=[BookLevel(price=ask, size=depth)])


def _cfg(**kw):
    base = dict(enabled=True, sports=("baseball",), stake_usd=1.0)
    base.update(kw)
    return TouchConfig(**base)


def test_config_parses_and_runs_per_sport():
    c = TouchConfig.from_cfg({"touch": {"enabled": True, "sports": "baseball", "improve": True,
                                        "min_spread_ticks": 2, "stake_usd": 2.5},
                              "execution": {"min_entry_price": 0.2, "max_entry_price": 0.8}})
    assert c.runs("baseball") and not c.runs("tennis")
    assert c.improve and c.min_spread_ticks == 2 and c.stake_usd == 2.5
    assert (c.min_entry_price, c.max_entry_price) == (0.2, 0.8)
    assert not TouchConfig.from_cfg({}).enabled
    with pytest.raises(ValueError):
        TouchConfig.from_cfg({"touch": {"sides": "sideways"}})


def test_join_rests_at_the_bid_then_the_ask_on_the_next_cycle():
    intent, why = evaluate_touch(_market(), _quote(0.44, 0.47), _cfg(), EMPTY, [], NO_FEE, now=NOW)
    assert intent is not None and intent.side == Side.YES
    assert intent.price == pytest.approx(0.44) and intent.prob == pytest.approx(0.455)
    assert intent.edge == pytest.approx(0.015)                 # the half-spread
    assert intent.size == pytest.approx(round(1.0 / 0.44, 2)) and intent.kelly_fraction == 0.0
    assert why.startswith("touch join") and why.endswith("maker")
    assert arm_of_intent("baseball", intent.reason) == "baseball/touch/maker"
    # YES already resting: the NO side goes up at 1 - ask, the same half-spread
    i2, _ = evaluate_touch(_market(), _quote(0.44, 0.47), _cfg(), EMPTY, [Side.YES], NO_FEE, now=NOW)
    assert i2.side == Side.NO and i2.price == pytest.approx(0.53) and i2.edge == pytest.approx(0.015)
    i3, why3 = evaluate_touch(_market(), _quote(0.44, 0.47), _cfg(), EMPTY,
                              [Side.YES, Side.NO], NO_FEE, now=NOW)
    assert i3 is None and "already resting" in why3
    # a one-tick book is fine to JOIN (that is what Result 15 measured)
    i4, _ = evaluate_touch(_market(), _quote(0.44, 0.45), _cfg(), EMPTY, [], NO_FEE, now=NOW)
    assert i4 is not None and i4.edge == pytest.approx(0.005)


def test_improve_needs_room_and_never_lets_the_two_sides_meet():
    c = _cfg(improve=True)
    i, why = evaluate_touch(_market(), _quote(0.44, 0.45), c, EMPTY, [], NO_FEE, now=NOW)
    assert i is None and "nowhere to improve" in why
    c2 = _cfg(improve=True, min_spread_ticks=2)
    assert "under 2 tick" in evaluate_touch(_market(), _quote(0.44, 0.45), c2, EMPTY, [], NO_FEE, now=NOW)[1]
    # two ticks: improving lands exactly on the mid, zero half-spread: refused
    i, why = evaluate_touch(_market(), _quote(0.44, 0.46), c, EMPTY, [], NO_FEE, now=NOW)
    assert i is None and "eats the half-spread" in why
    # three ticks: both sides fit one tick inside, each with half a tick of edge
    i3, _ = evaluate_touch(_market(), _quote(0.44, 0.47), c, EMPTY, [], NO_FEE, now=NOW)
    assert i3.side == Side.YES and i3.price == pytest.approx(0.45) and i3.edge == pytest.approx(0.005)
    i4, _ = evaluate_touch(_market(), _quote(0.44, 0.47), c, EMPTY, [Side.YES], NO_FEE, now=NOW)
    assert i4.side == Side.NO and i4.price == pytest.approx(0.54) and i4.edge == pytest.approx(0.005)
    # the two improved levels can never be the same level: a NO at 1-0.45 after
    # a YES at 0.45 would match our own order
    assert round(1.0 - i4.price, 4) > i3.price


def test_every_gate_fails_closed():
    m, q, c = _market(), _quote(0.44, 0.47), _cfg()
    assert evaluate_touch(_market(sport=Sport.TENNIS), q, c, EMPTY, [], NO_FEE, now=NOW)[1].startswith("touch arm not enabled")
    assert "no funded" in evaluate_touch(m, q, c, EMPTY, [], NO_FEE, now=NOW, style="none")[1]
    assert "no funded" in evaluate_touch(m, q, c, EMPTY, [], NO_FEE, now=NOW, style="taker")[1]
    assert "two-sided" in evaluate_touch(m, MarketQuote(market_id="m1", bid=None, ask=0.5, ts=NOW), c, EMPTY, [], NO_FEE, now=NOW)[1]
    assert "wider than" in evaluate_touch(m, _quote(0.30, 0.60), c, EMPTY, [], NO_FEE, now=NOW)[1]
    assert "under 2 tick" in evaluate_touch(m, _quote(0.44, 0.45), _cfg(min_spread_ticks=2), EMPTY, [], NO_FEE, now=NOW)[1]
    assert "tradable band" in evaluate_touch(m, _quote(0.90, 0.93), c, EMPTY, [], NO_FEE, now=NOW)[1]
    assert "start time unknown" in evaluate_touch(_market(start_time=None), q, c, EMPTY, [], NO_FEE, now=NOW)[1]
    assert "too close" in evaluate_touch(_market(start_time=NOW + timedelta(minutes=5)), q, c, EMPTY, [], NO_FEE, now=NOW)[1]
    assert "outside the quoting window" in evaluate_touch(_market(start_time=NOW + timedelta(hours=20)), q, c, EMPTY, [], NO_FEE, now=NOW)[1]
    held = {**EMPTY, "by_market": {"m1": 3.5}}
    assert "touch cap" in evaluate_touch(m, q, c, held, [], NO_FEE, now=NOW)[1]
    spent = {**EMPTY, "by_sport": {"baseball": 19.5}}
    assert "sport budget" in evaluate_touch(m, q, c, spent, [], NO_FEE, now=NOW, sport_budget=20.0)[1]
    # a maker fee bigger than the half-spread means no order, not a losing one
    assert "eats the half-spread" in evaluate_touch(m, _quote(0.44, 0.45), c, EMPTY, [],
                                                     lambda p, mid="": 0.01, now=NOW)[1]
    only_no, _ = evaluate_touch(m, q, _cfg(sides="no"), EMPTY, [], NO_FEE, now=NOW)
    assert only_no.side == Side.NO


def test_running_arms_add_touch_maker_only_for_quoted_enabled_sports():
    cfg = {"sports": {"baseball": {"enabled": True}, "tennis": {"enabled": False}},
           "execution": {"post_inside_spread": True},
           "touch": {"enabled": True, "sports": ["baseball", "tennis"]}}
    arms = running_arms_for(cfg)
    assert "baseball/touch/maker" in arms and "baseball/touch/taker" not in arms
    assert not any(a.startswith("tennis/") for a in arms)
    cfg["touch"]["enabled"] = False
    assert "baseball/touch/maker" not in running_arms_for(cfg)


def test_executor_reports_resting_sides_and_books_touch_fills_on_the_arm(tmp_path):
    store = Store(str(tmp_path / "t.sqlite"))
    ex = Executor(PaperExchange(starting_balance=100.0), store, mode="paper")
    intent, _ = evaluate_touch(_market(), _quote(0.44, 0.47), _cfg(), EMPTY, [], NO_FEE, now=NOW)
    ex.submit(intent, quote=_quote(0.44, 0.47))
    assert ex.resting_sides("m1") == {Side.YES} and ex.resting_sides("other") == set()
    assert store.exposure_by()["total"] == 0.0                   # resting, not exposure


def test_runner_touch_pass_posts_through_risk_and_tallies(tmp_path):
    from types import SimpleNamespace

    from sportsbot.bot.risk import RiskConfig, RiskManager
    from sportsbot.bot.runner import Runner
    from sportsbot.bot.strategy import StrategyConfig

    r = Runner.__new__(Runner)
    r.store = Store(str(tmp_path / "t.sqlite"))
    r.account = "sim"
    r.touch = _cfg()
    r.maker_fee_fn = lambda price, market_id="": 0.0
    r.executor = Executor(PaperExchange(starting_balance=100.0), r.store, mode="paper")
    r.risk = RiskManager(RiskConfig(min_minutes_before_start=10), r.store)
    scfg = StrategyConfig()
    scfg.sport_budget = {"baseball": 20.0}
    scfg.style_override = {"baseball": "both"}
    # the runner evaluates against the real clock: give the game a real future start
    soon = datetime.now(timezone.utc) + timedelta(hours=3)
    sm = SimpleNamespace(market=_market(start_time=soon), prediction=SimpleNamespace(prob_yes=0.5))
    q = MarketQuote(market_id="m1", bid=0.44, ask=0.47,
                    bids=[BookLevel(price=0.44, size=200.0)], asks=[BookLevel(price=0.47, size=200.0)])
    summary = {"orders": 0}
    assert r._touch_pass(sm, q, EMPTY, scfg, summary) is True
    assert summary["touch"]["posted"] == 1 and summary["orders"] == 1
    assert r.executor.resting_sides("m1") == {Side.YES}
    rows = r.store.conn.execute("SELECT action, reason FROM decisions").fetchall()
    assert len(rows) == 1 and rows[0][0] == "bet" and rows[0][1].startswith("touch join")
    # second pass posts the other side; third has nothing to post
    assert r._touch_pass(sm, q, EMPTY, scfg, summary) is True
    assert r.executor.resting_sides("m1") == {Side.YES, Side.NO}
    assert r._touch_pass(sm, q, EMPTY, scfg, summary) is False
    assert summary["touch"]["skipped"] == {"both": 1}
    # a sport the arm does not run for is untouched
    other = SimpleNamespace(market=_market(market_id="m2", sport=Sport.TENNIS, start_time=soon),
                            prediction=SimpleNamespace(prob_yes=0.5))
    assert r._touch_pass(other, q, EMPTY, scfg, summary) is False
    # unfunded sport: no order
    scfg.style_override = {"baseball": "none"}
    assert r._touch_pass(SimpleNamespace(market=_market(market_id="m3", start_time=soon),
                                         prediction=SimpleNamespace(prob_yes=0.5)),
                         q, EMPTY, scfg, summary) is False


def test_shipped_configs_carry_the_touch_arm_off_by_default_and_on_in_the_sim():
    import yaml

    from sportsbot.bot.portfolio import load_allocation

    d = yaml.safe_load(open("config/default.yaml"))
    s = yaml.safe_load(open("config/sim.yaml"))
    assert d["touch"]["enabled"] is False and s["touch"]["enabled"] is True
    # Result 15 + its addendum: MLB, NHL and NFL carry the touch edge; NBA
    # is not measurable yet and tennis has none, so neither is quoted.
    assert s["touch"]["sports"] == ["baseball", "hockey", "football"]
    assert d["touch"]["sports"] == s["touch"]["sports"]
    assert s["touch"]["stake_usd"] == 1.0 and s["touch"]["improve"] is False
    for sport in ("baseball", "hockey", "football"):
        assert f"{sport}/touch/maker" in running_arms_for(s)
        assert f"{sport}/touch/maker" not in running_arms_for(d)
    alloc = load_allocation("config/allocation.yaml")
    for sport in ("baseball", "hockey", "football"):
        st = alloc.setting(f"{sport}/touch/maker")
        assert st.learn is False and st.weight is None     # nothing funded by this file


def test_runner_drain_lets_resting_orders_live_out_the_ttl_then_shadows_them(tmp_path, monkeypatch):
    """A one-shot cycle must not leave touch orders `open` forever: drain
    reconciles until the TTL, then expires (and shadows) what is left."""
    from sportsbot.bot.runner import Runner
    from sportsbot.core.types import TapePrint
    from sportsbot.exchanges.base import ExchangeClient

    class Tape(ExchangeClient):
        exchange = Exchange.POLYMARKET

        def list_sports_markets(self, s):
            return []

        def get_quote(self, m):
            raise RuntimeError

        def place_order(self, o):
            raise RuntimeError

        def cancel_order(self, i):
            return True

        def get_open_orders(self):
            return []

        def get_positions(self):
            return []

        def get_balance(self):
            return 0.0

        def recent_trades(self, market, since):
            return [TapePrint(ts=since + timedelta(milliseconds=100), price_yes=0.44, size=1.0)]

    r = Runner.__new__(Runner)
    r.store = Store(str(tmp_path / "t.sqlite"))
    r.mode = "paper"
    r.executor = Executor(PaperExchange(data_client=Tape(), starting_balance=100.0), r.store,
                          mode="paper", order_ttl_seconds=0.2)
    soon = datetime.now(timezone.utc) + timedelta(hours=3)
    intent, _ = evaluate_touch(_market(start_time=soon), _quote(0.44, 0.47), _cfg(), EMPTY, [],
                               NO_FEE, now=datetime.now(timezone.utc))
    r.executor.submit(intent, quote=_quote(0.44, 0.47))
    out = r.drain_resting(poll_seconds=0.05)
    (o,) = r.store.all_orders()
    # the tape printed 1 contract through the bid within the window: 1 of 2.27 filled,
    # the rest cancelled at the TTL with the shadow recorded
    assert out["fills_booked"] == 1 and out["canceled"] == 1 and out["still_resting"] == 0
    assert o["status"] == "canceled" and o["filled"] == 1.0
    assert o["raw"]["tape_through"] == 1.0 and o["raw"]["tape_filled"] == 1.0
    assert r.store.all_bets("paper")[0]["arm"] == "baseball/touch/maker"


def test_touch_arm_quotes_unpriced_markets_in_its_sports_only(tmp_path):
    """NHL/NFL without a sharp line: the scanner drops them, the touch arm
    (no model) still rests at their touch; other drop reasons stay dropped
    and the decision row carries no model probability."""
    from sportsbot.bot.risk import RiskConfig, RiskManager
    from sportsbot.bot.runner import Runner, _Unpriced
    from sportsbot.bot.scanner import ScanDrop
    from sportsbot.bot.strategy import StrategyConfig

    r = Runner.__new__(Runner)
    r.store = Store(str(tmp_path / "t.sqlite"))
    r.account = "sim"
    r.touch = _cfg(sports=["baseball", "hockey"])
    r.maker_fee_fn = lambda price, market_id="": 0.0
    r.executor = Executor(PaperExchange(starting_balance=100.0), r.store, mode="paper")
    r.risk = RiskManager(RiskConfig(min_minutes_before_start=10), r.store)
    soon = datetime.now(timezone.utc) + timedelta(hours=3)
    nhl = _market(market_id="nhl1", sport=Sport.HOCKEY, start_time=soon)
    nfl = _market(market_id="nfl1", sport=Sport.FOOTBALL, start_time=soon)
    mlb = _market(market_id="mlb1", start_time=soon)
    drops = [ScanDrop(nhl, "no sharp lines on record", "no sharp line"),
             ScanDrop(nfl, "no sharp lines on record", "no sharp line"),      # not in touch.sports
             ScanDrop(mlb, "unmatched entity", "unmatched"),                   # wrong category
             ScanDrop(_market(market_id="t1", sport=Sport.TENNIS, start_time=soon), "x", "no model")]
    assert [m.market_id for m in r._touch_only_markets(drops)] == ["nhl1"]
    r.touch = _cfg(sports=["baseball", "hockey"], enabled=False)
    assert r._touch_only_markets(drops) == []
    r.touch = _cfg(sports=["baseball", "hockey"])

    scfg = StrategyConfig()
    scfg.sport_budget = {"hockey": 20.0}
    scfg.style_override = {"hockey": "maker"}
    q = MarketQuote(market_id="nhl1", bid=0.44, ask=0.47,
                    bids=[BookLevel(price=0.44, size=200.0)], asks=[BookLevel(price=0.47, size=200.0)])
    summary = {"orders": 0}
    assert r._touch_pass(_Unpriced(nhl), q, EMPTY, scfg, summary) is True
    assert r.executor.resting_sides("nhl1") == {Side.YES}
    rows = [tuple(x) for x in r.store.conn.execute(
        "SELECT action, sport, model_prob, reason FROM decisions").fetchall()]
    assert rows == [("bet", "hockey", None, rows[0][3])] and rows[0][3].startswith("touch join")


def test_allocator_does_not_gate_the_touch_arm_on_a_priceable_sport():
    from sportsbot.bot.portfolio import AllocationConfig, allocate_arms

    cfg = {"bankroll": {"max_total_exposure": 0.5, "max_fraction_per_sport": 0.2},
           "sports": {}, "execution": {}}
    running = {"baseball/model/maker", "hockey/sharp/maker", "hockey/touch/maker"}
    port = allocate_arms(100.0, cfg, AllocationConfig(), [], running, mode="paper",
                         priceable_sports={"baseball"})
    assert "nothing can price" in port["arms"]["hockey/sharp/maker"]["source"]
    assert port["arms"]["hockey/sharp/maker"]["budget"] == 0.0
    assert port["arms"]["hockey/touch/maker"]["budget"] == 20.0     # equal share, per-sport cap
    assert port["sport_budget"] == {"baseball": 20.0, "hockey": 20.0}
    assert port["style_by_sport"]["hockey"] == "maker"
    # live: still only manual weight / evidence / learn — nothing funded here
    live = allocate_arms(100.0, cfg, AllocationConfig(), [], running, mode="live",
                         priceable_sports={"baseball"})
    assert live["arms"]["hockey/touch/maker"]["budget"] == 0.0
    assert "gate not met" in live["arms"]["hockey/touch/maker"]["source"]


def test_shipped_maker_test_config_is_inert_and_touch_only():
    """config/maker_test.yaml: the pre-registered live test, shipped in paper
    mode on Kalshi with the touch arm as its only funded path."""
    import yaml

    c = yaml.safe_load(open("config/maker_test.yaml"))
    assert c["mode"] == "paper" and c["exchange"] == "kalshi"
    assert c["accounts"]["real"]["starting_balance"] == 0.0
    t = c["touch"]
    assert t["enabled"] is True and t["sports"] == ["baseball", "hockey", "football"]
    assert t["improve"] is False and t["stake_usd"] == 1.0 and t["max_stake_per_market"] == 4.0
    assert c["bankroll"]["min_stake"] <= t["stake_usd"]
    assert c["bankroll"]["max_stake_per_market"] >= t["max_stake_per_market"]
    assert c["execution"]["order_ttl_seconds"] == 120           # the measured deadline
    assert c["sports"]["tennis"]["enabled"] is False and c["sports"]["table_tennis"]["enabled"] is False
    assert c["sports"]["basketball"]["enabled"] is False
    assert c["allocation"]["path"] == "config/allocation.yaml"  # funding stays the operator's file
    assert c["risk"]["daily_loss_fraction"] == 0.10 and c["risk"]["max_drawdown_fraction"] == 0.25
    assert c["risk"]["daily_loss_limit"] <= 10.0 and c["risk"]["max_drawdown"] <= 25.0
    assert set(c["scan"]["sports"]) == {"baseball", "hockey", "football"}
    arms = running_arms_for(c)
    assert {"baseball/touch/maker", "hockey/touch/maker", "football/touch/maker"} <= arms
    assert not any(a.startswith("tennis/") for a in arms)
