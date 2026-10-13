"""A restart of the continuously running sim must lose no maker evidence:
SIGTERM cancels resting orders with their shadow; startup sweeps orphans."""

import time
from datetime import datetime, timedelta, timezone

from sportsbot.bot.executor import Executor
from sportsbot.bot.maker_report import format_maker_report, maker_report
from sportsbot.bot.touch import TouchConfig, evaluate_touch
from sportsbot.core.types import (BookLevel, Exchange, MarketInfo, MarketQuote, Order, OrderStatus,
                                  Side, Sport)
from sportsbot.data.store import Store
from sportsbot.exchanges.base import ExchangeClient
from sportsbot.exchanges.paper import PaperExchange

EMPTY = {"total": 0.0, "by_sport": {}, "by_market": {}, "open_positions": 0}


class _Tape(ExchangeClient):
    """Public-tape source: one seller hits the bid 0.5 s after any order rests."""
    exchange = Exchange.POLYMARKET

    def list_sports_markets(self, s):
        return []

    def get_quote(self, m):
        raise RuntimeError

    def place_order(self, order):
        return order

    def cancel_order(self, order_id):
        return True

    def get_open_orders(self):
        return []

    def get_positions(self):
        return []

    def get_balance(self):
        return 0.0

    def get_resolution(self, market_id):
        return None

    def recent_trades(self, market, since):
        from sportsbot.core.types import TapePrint
        return [TapePrint(ts=since + timedelta(milliseconds=1), price_yes=0.44, size=3.0, taker_side="sell")]


def _market(**kw):
    d = dict(exchange=Exchange.POLYMARKET, market_id="m1", yes_token_id="t1", no_token_id="t2",
             slug="nyy-bos", sport=Sport.BASEBALL, home="New York Yankees", away="Boston Red Sox",
             start_time=datetime.now(timezone.utc) + timedelta(hours=3))
    d.update(kw)
    return MarketInfo(**d)


def _quote(mid="m1"):
    return MarketQuote(market_id=mid, bid=0.44, ask=0.47,
                       bids=[BookLevel(price=0.44, size=200.0)], asks=[BookLevel(price=0.47, size=200.0)])


def _rest_one(ex, market=None):
    market = market or _market()
    intent, why = evaluate_touch(market, _quote(market.market_id), TouchConfig(enabled=True, sports=("baseball",)),
                                 EMPTY, set(), lambda p, market_id="": 0.0, sport_budget=20.0, style="maker")
    assert intent is not None, why
    order = ex.submit(intent, quote=_quote(market.market_id))
    assert order.status == OrderStatus.OPEN
    return order


def test_cancel_all_resting_ignores_the_ttl_and_records_the_reason(tmp_path):
    store = Store(str(tmp_path / "t.sqlite"))
    ex = Executor(PaperExchange(data_client=_Tape(), starting_balance=100.0), store, mode="paper",
                  order_ttl_seconds=120.0)
    o1 = _rest_one(ex)
    o2 = _rest_one(ex, _market(market_id="m2", slug="lad-sd"))
    assert ex.resting_sides("m1") == {Side.YES} and ex.resting_sides("m2") == {Side.YES}
    assert ex.expire_stale_orders() == 0                      # fresh: the TTL has not passed
    time.sleep(0.05)                                          # let the tape print fall inside the rest window
    assert ex.cancel_all_resting("shutdown") == 2
    assert ex.resting_sides("m1") == set() and ex._open == {}
    rows = {o["client_id"]: o for o in store.all_orders()}
    for o in (o1, o2):
        r = rows[o.client_id]
        assert r["status"] == "canceled" and r["raw"]["canceled_reason"] == "shutdown"
        assert r["raw"]["tape_through"] == round(min(o.size, 3.0), 6)   # the shadow: min(order size, printed)


def test_sweep_orphans_closes_paper_rows_left_resting_by_a_dead_process(tmp_path):
    store = Store(str(tmp_path / "t.sqlite"))
    store.record_order(client_id="dead1", order_id="dead1", market_id="m1", side="yes", price=0.44,
                       size=2.0, filled=0.0, status="open", raw={"arm": "baseball/touch/maker"})
    store.record_order(client_id="dead2", order_id="dead2", market_id="m2", side="no", price=0.53,
                       size=2.0, filled=0.0, status="pending", raw={"arm": "baseball/touch/maker"})
    store.record_order(client_id="done", order_id="done", market_id="m3", side="yes", price=0.5,
                       size=2.0, filled=2.0, status="filled", raw={"arm": "baseball/touch/maker"})
    ex = Executor(PaperExchange(starting_balance=100.0), store, mode="paper")
    assert ex.sweep_orphans() == 2
    rows = {o["client_id"]: o for o in store.all_orders()}
    for cid in ("dead1", "dead2"):
        assert rows[cid]["status"] == "canceled" and rows[cid]["raw"]["orphaned"] is True
        assert rows[cid]["raw"]["arm"] == "baseball/touch/maker"      # existing raw kept
        assert rows[cid]["raw"]["canceled_reason"] == "orphaned_on_restart"
    assert rows["done"]["status"] == "filled" and "orphaned" not in rows["done"]["raw"]
    assert ex.sweep_orphans() == 0                                     # idempotent
    rep = maker_report(store, signal="touch")
    assert rep["orphaned"] == 2 and rep["by_status"]["canceled"] == 2
    assert "orphaned by a restart" in format_maker_report(rep)


def test_sweep_orphans_live_cancels_ours_on_the_venue_and_leaves_unknown_orders(tmp_path):
    store = Store(str(tmp_path / "t.sqlite"))
    store.record_order(client_id="c-ours", order_id="v-ours", market_id="m1", side="yes", price=0.44,
                       size=2.0, filled=0.0, status="open", raw={"arm": "baseball/touch/maker"})
    canceled = []

    class Venue(_Tape):
        exchange = Exchange.KALSHI

        def cancel_order(self, order_id):
            canceled.append(order_id)
            return True

        def get_open_orders(self):
            return [Order(order_id="v-ours", client_id="c-ours", market_id="m1", side=Side.YES,
                          price=0.44, size=2.0, status=OrderStatus.OPEN),
                    Order(order_id="v-manual", client_id="c-manual", market_id="m9", side=Side.NO,
                          price=0.30, size=5.0, status=OrderStatus.OPEN)]

    ex = Executor(Venue(), store, mode="live")
    assert ex.sweep_orphans() == 1
    assert canceled == ["v-ours"]                                      # the manual order is left alone
    row = {o["client_id"]: o for o in store.all_orders()}["c-ours"]
    assert row["status"] == "canceled" and row["raw"]["orphaned"] is True


def test_run_forever_stops_on_request_and_cancels_resting_orders(tmp_path, monkeypatch):
    from sportsbot.bot.runner import Runner

    r = Runner.__new__(Runner)
    r.cfg = {"scan": {"interval_seconds": 60}}
    r.mode = "paper"
    r.store = Store(str(tmp_path / "t.sqlite"))
    r.exchange = PaperExchange(starting_balance=100.0)
    r.executor = Executor(r.exchange, r.store, mode="paper", order_ttl_seconds=120.0)
    order = _rest_one(r.executor)
    cycles = []

    def fake_cycle():
        cycles.append(1)
        r.request_stop()           # what SIGTERM does
        return {"markets": 0}

    monkeypatch.setattr(r, "cycle", fake_cycle)
    r.run_forever()
    assert cycles == [1]
    row = {o["client_id"]: o for o in r.store.all_orders()}[order.client_id]
    assert row["status"] == "canceled" and row["raw"]["canceled_reason"] == "shutdown"
    assert r.executor._open == {}
