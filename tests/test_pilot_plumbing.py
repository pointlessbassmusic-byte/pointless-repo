"""Live-pilot plumbing: paper/live book isolation, allocation wired into
sizing (with the operator's manual overrides), and fill accounting that
survives an order leaving the book or the process restarting."""

from datetime import datetime, timedelta, timezone

from sportsbot.bot.allocation import allocate
from sportsbot.bot.executor import Executor
from sportsbot.bot.strategy import StrategyConfig, evaluate_market_verbose
from sportsbot.core.staking import StakingConfig
from sportsbot.core.types import (
    BetIntent,
    BookLevel,
    Exchange,
    MarketInfo,
    MarketQuote,
    Order,
    OrderStatus,
    Prediction,
    Side,
    Sport,
)
from sportsbot.data.store import Store


def _market(**kw):
    defaults = dict(
        exchange=Exchange.KALSHI, market_id="KXT-A", yes_token_id="t1",
        no_token_id="t2", question="A vs B", slug="a-b", sport=Sport.TENNIS,
        home="Alpha", away="Beta",
        start_time=datetime.now(timezone.utc) + timedelta(hours=6),
    )
    defaults.update(kw)
    return MarketInfo(**defaults)


def _quote(bid=0.48, ask=0.50, depth=500.0):
    return MarketQuote(market_id="KXT-A", bid=bid, ask=ask,
                       bids=[BookLevel(price=bid, size=depth)],
                       asks=[BookLevel(price=ask, size=depth)])


def _bet(store, mode, stake=10.0, sport="tennis", market="m"):
    return store.record_bet(market_id=market, sport=sport, side="yes", model_prob=0.6,
                            entry_price=0.5, stake=stake, size=stake / 0.5, edge=0.05,
                            exchange="kalshi", mode=mode)


# ---------------------------------------------------------------- books
def test_exposure_and_daily_stake_are_per_book(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    _bet(store, "paper", 40.0, market="p1")
    _bet(store, "live", 7.0, market="l1")
    assert store.exposure_by()["total"] == 47.0            # unfiltered: everything
    assert store.exposure_by(mode="live")["total"] == 7.0
    assert store.exposure_by(mode="paper")["by_sport"] == {"tennis": 40.0}
    assert {r["mode"] for r in store.bets_today(mode="live")} == {"live"}
    assert len(store.open_bets(mode="paper")) == 1


# ----------------------------------------------------------- allocation
def _cfg(**alloc):
    return {
        "sports": {"baseball": {"enabled": True}, "tennis": {"enabled": True},
                   "table_tennis": {"enabled": True}},
        "bankroll": {"max_fraction_per_sport": 0.5, "max_total_exposure": 1.0},
        "adaptive": {"clv_min_bets": 30},
        "allocation": alloc,
    }


def test_manual_weights_replace_priors_and_paused_gets_nothing():
    ratings = {"tennis": True}
    base = allocate(100.0, _cfg(), has_ratings=ratings)["sleeves"]
    assert base["baseball"]["budget"] > base["tennis"]["budget"]   # evidence priors

    manual = allocate(100.0, _cfg(manual={"tennis": 0.8, "baseball": 0.2},
                                  paused=["table_tennis"]), has_ratings=ratings)
    s = manual["sleeves"]
    assert s["tennis"]["budget"] > s["baseball"]["budget"]
    assert s["tennis"]["bound_by"] == "per-sport cap"             # 0.8 > the 0.5 cap
    assert s["tennis"]["manual"] and s["baseball"]["bound_by"] == "manual weight"
    assert s["table_tennis"]["budget"] == 0.0 and not s["table_tennis"]["active"]
    assert "paused" in s["table_tennis"]["bound_by"]


def test_manual_weight_cannot_override_a_negative_measurement():
    by_sport = {"baseball": {"mean_clv": -0.05, "n_clv": 40, "n": 40}}
    cfg = _cfg(manual={"baseball": 0.5, "tennis": 0.5})
    s = allocate(100.0, cfg, by_sport, has_ratings={"tennis": True})["sleeves"]
    assert s["baseball"]["clv_multiplier"] == 0.6                  # 1 + 8 * -0.05
    assert s["baseball"]["weight"] < 0.5 < s["tennis"]["weight"]  # measurement wins
    assert s["baseball"]["bound_by"] == "CLV-adjusted share"


def test_sleeve_budget_tightens_the_sport_cap_but_never_loosens_it():
    staking = StakingConfig(bankroll=1000.0, kelly_multiplier=1.0, min_edge=0.01,
                            min_stake=1.0, max_stake_per_market=500.0,
                            max_fraction_per_market=0.5, max_fraction_per_sport=0.20,
                            max_total_exposure=1.0, max_open_positions=50)
    fee = lambda p, s=1.0, m="": 0.0   # noqa: E731
    exp = {"total": 0.0, "by_sport": {}, "by_market": {}, "by_event": {}, "open_positions": 0}
    pred = Prediction(market_id="KXT-A", sport=Sport.TENNIS, model="t", prob_yes=0.75,
                      uncertainty=0.02)
    base = StrategyConfig(post_inside_spread=False, model_weight=1.0)
    free, _ = evaluate_market_verbose(_market(), _quote(), pred, staking, base, fee, exp)
    assert free is not None and free.price * free.size > 50.0

    tight = StrategyConfig(post_inside_spread=False, model_weight=1.0,
                           sport_budget_override={"tennis": 12.0})
    intent, _ = evaluate_market_verbose(_market(), _quote(), pred, staking, tight, fee, exp)
    assert intent is not None and intent.price * intent.size <= 12.0 + 1e-6

    loose = StrategyConfig(post_inside_spread=False, model_weight=1.0,
                           sport_budget_override={"tennis": 5000.0})
    same, _ = evaluate_market_verbose(_market(), _quote(), pred, staking, loose, fee, exp)
    assert abs(same.price * same.size - free.price * free.size) < 1e-6   # cap still 20%


# -------------------------------------------------------------- fills
class _Venue:
    """Minimal live venue: orders rest, then leave the book; get_order
    reports the final fill."""
    exchange = Exchange.KALSHI

    def __init__(self, final_fill):
        self.final_fill = final_fill
        self.resting = True
        self.canceled = []

    def place_order(self, order):
        order.order_id = "o1"
        order.status = OrderStatus.OPEN
        return order

    def get_open_orders(self):
        return []

    def get_order(self, order_id):
        return Order(order_id=order_id, filled=self.final_fill, size=10.0,
                     status=OrderStatus.FILLED)

    def cancel_order(self, order_id):
        self.canceled.append(order_id)
        return True


def _intent():
    return BetIntent(intent_id="i1", market=_market(), side=Side.YES, prob=0.6,
                     price=0.49, size=10.0, edge=0.05, kelly_fraction=0.01, reason="t")


def test_order_that_left_the_book_is_booked_from_the_venue_fill(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    ex = Executor(_Venue(final_fill=10.0), store, mode="live")
    ex.submit(_intent())
    assert store.exposure_by(mode="live")["total"] == 0.0      # resting, not exposure
    ex.reconcile_open_orders()
    bets = store.open_bets(mode="live")
    assert len(bets) == 1 and bets[0]["size"] == 10.0 and bets[0]["stake"] == 4.9
    row = store.open_orders_rows()
    assert row == []                                           # persisted as filled


def test_restart_books_the_missed_fill_and_cancels_the_rest(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    venue = _Venue(final_fill=4.0)
    Executor(venue, store, mode="live").submit(_intent())     # process "dies" here
    assert store.open_orders_rows()[0]["raw"]["intent"]["sport"] == "tennis"

    fresh = Executor(venue, store, mode="live")
    assert fresh.restore_open_orders() == 1
    bets = store.open_bets(mode="live")
    assert len(bets) == 1 and bets[0]["size"] == 4.0 and bets[0]["model_prob"] == 0.6
    assert venue.canceled == ["o1"]
    assert store.open_orders_rows() == []
    assert fresh.restore_open_orders() == 0                   # idempotent


def test_polymarket_matched_order_reports_its_fill(monkeypatch):
    from sportsbot.exchanges.polymarket import PolymarketClient

    class _Sdk:
        def place_limit_order(self, **kw):
            return {"status": "matched", "orderID": "abc", "size_matched": "3"}

    client = PolymarketClient(private_key="k")
    monkeypatch.setattr(client, "_sdk_client", lambda: _Sdk())
    order = client.place_order(Order(token_id="t1", price=0.5, size=3.0))
    assert order.status == OrderStatus.FILLED and order.filled == 3.0


def test_kalshi_parse_order_maps_final_states():
    from sportsbot.exchanges.kalshi import KalshiClient

    client = KalshiClient.__new__(KalshiClient)
    done = client._parse_order({"order_id": "o", "ticker": "T", "side": "yes",
                                "status": "executed", "fill_count_fp": "5.00",
                                "initial_count_fp": "5.00", "yes_price_dollars": "0.4000"})
    assert done.status == OrderStatus.FILLED and done.filled == 5.0
    gone = client._parse_order({"order_id": "o", "ticker": "T", "side": "no",
                                "status": "canceled", "fill_count_fp": "2.00",
                                "initial_count_fp": "5.00", "no_price_dollars": "0.6000"})
    assert gone.status == OrderStatus.CANCELED and gone.filled == 2.0 and gone.side == Side.NO


# ------------------------------------------------------------- doctor
def test_doctor_refuses_a_live_book_on_the_sim_db_or_unfunded(monkeypatch):
    from sportsbot.bot.doctor import FAIL, PASS, check_mode

    monkeypatch.setenv("SPORTSBOT_LIVE", "1")
    bad = {"mode": "live", "exchange": "kalshi",
           "storage": {"sqlite_path": "data/sportsbot.sqlite"},
           "accounts": {"real": {"starting_balance": 0.0}}, "bankroll": {"amount": 150.0}}
    levels = {c.name: c.level for c in check_mode(bad)}
    assert levels["live.own_db"] == FAIL and levels["live.funded"] == FAIL

    good = {"mode": "live", "exchange": "kalshi",
            "storage": {"sqlite_path": "data/pilot.sqlite"},
            "accounts": {"real": {"starting_balance": 150.0}}, "bankroll": {"amount": 150.0}}
    levels = {c.name: c.level for c in check_mode(good)}
    assert levels["live.own_db"] == levels["live.funded"] == PASS
    assert levels["live.bankroll_le_deposit"] == PASS

    over = dict(good, bankroll={"amount": 400.0})
    assert {c.name: c.level for c in check_mode(over)}["live.bankroll_le_deposit"] == FAIL


def test_pilot_config_passes_the_offline_doctor_shape():
    import yaml

    from sportsbot.bot.doctor import FAIL, check_mode, check_params

    cfg = yaml.safe_load(open("config/pilot.yaml"))
    assert cfg["mode"] == "live" and cfg["storage"]["sqlite_path"] != "data/sportsbot.sqlite"
    assert all(c.level != FAIL for c in check_mode(cfg) + check_params(cfg))


# ---------------------------------------------------------- dashboard
def test_board_shows_learning_log_and_research_ledger(tmp_path):
    from sportsbot.dashboard import collect, load_ledger, render

    store = Store(str(tmp_path / "s.sqlite"))
    store.record_allocation("sim", "baseball", 12.5, 0.55, "evidence prior", None, 0, False)
    cfg = {"sports": {"baseball": {"enabled": True}}, "bankroll": {"amount": 100.0},
           "accounts": {"sim": {"starting_balance": 100.0}},
           "storage": {"ratings_dir": str(tmp_path)}}
    data = collect(cfg, store)
    assert data["accounts"]["sim"]["allocation_log"][0]["sport"] == "baseball"
    assert load_ledger() and all("verdict" in e for e in load_ledger())
    html = render(data)
    assert "Learning log" in html and "$12.50" in html
    assert "Research ledger" in html and "FAIL" in html


# ------------------------------------------------ review regressions
class _Flaky(_Venue):
    """get_order fails N times before answering; cancel never called."""

    def __init__(self, final_fill, failures):
        super().__init__(final_fill)
        self.failures = failures
        self.lookups = 0

    def get_order(self, order_id):
        self.lookups += 1
        if self.lookups <= self.failures:
            raise RuntimeError("venue 503")
        return super().get_order(order_id)


def test_transient_lookup_failure_keeps_the_order_tracked_until_the_fill_is_known(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    venue = _Flaky(final_fill=10.0, failures=2)
    ex = Executor(venue, store, mode="live")
    ex.submit(_intent())
    ex.reconcile_open_orders()                      # lookup fails: still tracked
    ex.reconcile_open_orders()                      # fails again
    assert store.open_bets(mode="live") == [] and len(ex._open) == 1
    assert store.open_orders_rows()[0]["status"] == "open"   # never marked canceled
    ex.reconcile_open_orders()                      # third lookup succeeds
    assert store.open_bets(mode="live")[0]["size"] == 10.0 and ex._open == {}


def test_partial_fill_reconcile_keeps_the_stored_intent_for_restart(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))

    class _Partial(_Venue):
        def get_open_orders(self):
            return [Order(order_id="o1", filled=3.0, size=10.0, status=OrderStatus.PARTIAL)]

    ex = Executor(_Partial(final_fill=10.0), store, mode="live")
    ex.submit(_intent())
    ex.reconcile_open_orders()                      # books 3, re-persists the row
    row = store.open_orders_rows()[0]
    assert row["filled"] == 3.0 and row["raw"]["intent"]["prob"] == 0.6


def test_restore_books_a_fill_even_when_the_row_has_no_intent(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    store.record_order(client_id="old", order_id="o1", market_id="KXT-A", side="yes",
                       price=0.49, size=10.0, filled=0.0, status="open", raw={})   # pre-upgrade row
    venue = _Venue(final_fill=10.0)
    assert Executor(venue, store, mode="live").restore_open_orders() == 1
    bets = store.open_bets(mode="live")
    assert len(bets) == 1 and bets[0]["size"] == 10.0 and bets[0]["sport"] == "unknown"
