"""The maker fill-share test's evidence trail: every order carries the
arm, the book, the timestamps and (after it leaves the book) the tape
shadow; the report turns that into fill share, CLV per fill and
adverse selection by wait."""

from datetime import datetime, timedelta, timezone

import pytest

from sportsbot.bot.executor import Executor
from sportsbot.bot.maker_report import format_maker_report, maker_report
from sportsbot.core.types import (
    BetIntent,
    BookLevel,
    Exchange,
    MarketInfo,
    MarketQuote,
    Order,
    OrderStatus,
    Side,
    Sport,
    TapePrint,
)
from sportsbot.data.store import Store
from sportsbot.exchanges.base import ExchangeClient
from sportsbot.exchanges.paper import PaperExchange

T0 = datetime(2026, 10, 13, 0, 0, tzinfo=timezone.utc)


def _pr(offset_s, price, size=10.0):
    return TapePrint(ts=T0 + timedelta(seconds=offset_s), price_yes=price, size=size)


class Tape(ExchangeClient):
    exchange = Exchange.POLYMARKET

    def __init__(self, prints):
        self.prints = prints

    def list_sports_markets(self, sport_tag):
        return []

    def get_quote(self, market):
        raise RuntimeError

    def place_order(self, order):
        raise RuntimeError

    def cancel_order(self, order_id):
        return True

    def get_open_orders(self):
        return []

    def get_positions(self):
        return []

    def get_balance(self):
        return 0.0

    def recent_trades(self, market, since):
        return [p for p in self.prints if p.ts >= since]


class LiveStub(Tape):
    """A 'live' venue: orders rest until the test fills or drops them."""
    exchange = Exchange.KALSHI

    def __init__(self, prints):
        super().__init__(prints)
        self.resting: dict[str, Order] = {}

    def place_order(self, order):
        order.order_id = "live-" + order.client_id[:6]
        order.status = OrderStatus.OPEN
        self.resting[order.order_id] = order.model_copy()   # the venue's own record
        return order

    def get_open_orders(self):
        return list(self.resting.values())

    def cancel_order(self, order_id):
        return self.resting.pop(order_id, None) is not None


def _market(**kw):
    d = dict(exchange=Exchange.POLYMARKET, market_id="m1", yes_token_id="t1", no_token_id="t2",
             slug="a-b", sport=Sport.BASEBALL, home="A", away="B", start_time=T0 + timedelta(hours=4))
    d.update(kw)
    return MarketInfo(**d)


def _quote(bid=0.44, ask=0.47):
    return MarketQuote(market_id="m1", bid=bid, ask=ask, ts=T0,
                       bids=[BookLevel(price=bid, size=120.0)], asks=[BookLevel(price=ask, size=80.0)])


def _touch_intent(price=0.44, side=Side.YES, size=2.0, market=None):
    return BetIntent(market=market or _market(), side=side, prob=0.455, price=price, size=size,
                     edge=0.015, kelly_fraction=0.0,
                     reason="touch join bid=0.440 ask=0.470 spread=0.030 half=0.0150 fee=0.0000 maker")


def test_submit_stamps_arm_book_and_times_on_the_order(tmp_path):
    store = Store(str(tmp_path / "t.sqlite"))
    ex = Executor(PaperExchange(starting_balance=100.0), store, mode="paper")
    ex.submit(_touch_intent(), quote=_quote())
    (o,) = store.all_orders()
    assert o["raw"]["arm"] == "baseball/touch/maker" and o["raw"]["reason"].startswith("touch join")
    assert o["raw"]["book"] == {"bid": 0.44, "ask": 0.47, "bid_size": 120.0, "ask_size": 80.0}
    assert o["raw"]["placed_at"] and o["raw"]["start_time"] and o["status"] == "open"


def test_paper_expiry_and_fill_record_the_tape_shadow(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "t.sqlite"))
    tape = Tape([_pr(20, 0.44, 1.0), _pr(500, 0.40, 50.0)])          # 1 contract through within TTL
    paper = PaperExchange(data_client=tape, starting_balance=100.0)
    ex = Executor(paper, store, mode="paper", order_ttl_seconds=120.0)
    # pin the clock: placement at T0, reconcile at T0+60, expiry at T0+200
    import sportsbot.bot.executor as execmod
    clock = {"now": T0}

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]
    monkeypatch.setattr(execmod, "datetime", _DT)
    ex.submit(_touch_intent(size=2.0), quote=_quote())
    clock["now"] = T0 + timedelta(seconds=60)
    paper_now = clock["now"]
    paper.reconcile_resting = (lambda orig: lambda ttl_seconds=None: orig(now=paper_now, ttl_seconds=ttl_seconds))(paper.reconcile_resting)
    ex.reconcile_open_orders()                                      # partial: 1 of 2 via the tape
    (o,) = store.all_orders()
    assert o["filled"] == 1.0 and o["status"] == "partial"
    assert o["raw"]["first_fill_at"] == (T0 + timedelta(seconds=60)).isoformat()
    assert o["raw"]["rested_seconds"] == 60.0
    clock["now"] = T0 + timedelta(seconds=200)
    assert ex.expire_stale_orders() == 1
    (o,) = store.all_orders()
    assert o["status"] == "canceled"
    # the shadow is bounded by the TTL: the 500 s print does not count
    assert o["raw"]["tape_through"] == 1.0 and o["raw"]["tape_filled"] == 1.0
    rep = maker_report(store)
    assert rep["paper_self_check_mismatches"] == 0
    assert rep["fill_share_orders"] == 1.0 and rep["fill_share_contracts"] == 1.0


def test_live_order_leaving_the_book_gets_a_shadow_and_fills_get_timed(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "t.sqlite"))
    venue = LiveStub([_pr(10, 0.44, 5.0)])
    ex = Executor(venue, store, mode="live", order_ttl_seconds=120.0)
    import sportsbot.bot.executor as execmod
    clock = {"now": T0}

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]
    monkeypatch.setattr(execmod, "datetime", _DT)
    order = ex.submit(_touch_intent(size=2.0), quote=_quote())
    assert order.status == OrderStatus.OPEN and ex.resting_sides("m1") == {Side.YES}
    # the venue reports a partial fill 15 s later
    clock["now"] = T0 + timedelta(seconds=15)
    venue.resting[order.order_id].filled = 1.0
    ex.reconcile_open_orders()
    (o,) = store.all_orders()
    assert o["filled"] == 1.0 and o["raw"]["rested_seconds"] == 15.0
    assert len(store.all_bets("live")) == 1 and store.all_bets("live")[0]["arm"] == "baseball/touch/maker"
    # it then disappears from the book (filled or pulled): shadow taken, order untracked
    clock["now"] = T0 + timedelta(seconds=40)
    venue.resting.clear()
    ex.reconcile_open_orders()
    (o,) = store.all_orders()
    assert o["raw"]["tape_through"] == 2.0 and o["raw"]["tape_prints"] == 1   # 5 through, capped at size
    assert ex.resting_sides("m1") == set()
    rep = maker_report(store, mode="live")
    assert rep["orders"] == 1 and rep["orders_filled"] == 1 and rep["tape_would_fill_orders"] == 1
    assert rep["fill_share_contracts"] == pytest.approx(0.5)      # 1 real of 2 the tape credits


def test_report_grades_fills_against_the_close_and_buckets_by_wait(tmp_path):
    store = Store(str(tmp_path / "t.sqlite"))
    # three filled touch orders on three markets, one unfilled, one with no shadow yet
    specs = [("m1", "yes", 0.44, 10.0, 2.0, 2.0),    # wait 10 s, close 0.50 -> +0.06
             ("m2", "no", 0.53, 60.0, 2.0, 2.0),     # wait 60 s, close 0.50 -> side close 0.50 -> -0.03
             ("m3", "yes", 0.40, 300.0, 1.0, 2.0),   # wait 300 s, close 0.38 -> -0.02
             ("m4", "yes", 0.45, None, 0.0, 0.0),    # never filled, tape never through
             ("m5", "yes", 0.45, None, 0.0, None)]   # still resting: no shadow
    for i, (mk, side, px, wait, filled, through) in enumerate(specs):
        raw = {"arm": "baseball/touch/maker", "placed_at": T0.isoformat()}
        if wait is not None:
            raw["rested_seconds"] = wait
        if through is not None:
            raw["tape_through"] = through
        store.record_order(client_id=f"c{i}", order_id=f"o{i}", market_id=mk, side=side,
                           price=px, size=2.0, filled=filled,
                           status="filled" if filled else "open", raw=raw)
    for mk, side, px, close, pnl in (("m1", "yes", 0.44, 0.50, 1.0), ("m2", "no", 0.53, 0.50, -1.0),
                                     ("m3", "yes", 0.40, 0.38, -0.5)):
        bid = store.record_bet(market_id=mk, sport="baseball", side=side, model_prob=0.5,
                               entry_price=px, stake=px * 2, size=2.0, edge=0.01,
                               exchange="paper", mode="paper", arm="baseball/touch/maker")
        store.settle_bet(bid, outcome=1, pnl=pnl, closing_price=close)
    # a model-arm bet must not leak into the touch read-out
    store.record_bet(market_id="m9", sport="baseball", side="yes", model_prob=0.6, entry_price=0.5,
                     stake=1.0, size=2.0, edge=0.1, exchange="paper", mode="paper",
                     arm="baseball/model/taker")
    rep = maker_report(store)
    assert rep["orders"] == 5 and rep["orders_filled"] == 3 and rep["orders_shadowed"] == 4
    assert rep["tape_would_fill_orders"] == 3 and rep["fill_share_orders"] == pytest.approx(1.0)
    assert rep["fill_share_contracts"] == pytest.approx(5.0 / 6.0)
    assert rep["fills"] == 3 and rep["graded_venue"] == 3 and rep["bets"] == 3
    assert rep["clv_venue"]["mean"] == pytest.approx((0.06 - 0.03 - 0.02) / 3)
    assert {k: v["n"] for k, v in rep["by_wait"].items()} == {"< 30 s": 1, "30–120 s": 1, "> 120 s": 1}
    assert rep["by_wait"]["< 30 s"]["clv"]["mean"] == pytest.approx(0.06)
    assert rep["gate"] == {"fills": 3, "needed": 200, "lower_bound_positive": False}
    assert rep["realised_pnl"] == pytest.approx(-0.5)
    text = format_maker_report(rep)
    assert "fill share: orders 100.0%" in text and "wait < 30 s" in text and "3/200 graded fills" in text
    assert "nothing posted yet" in format_maker_report(maker_report(store, signal="sharp"))


def test_cli_maker_report_runs_offline(tmp_path):
    from typer.testing import CliRunner

    from sportsbot.cli import app

    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"storage:\n  sqlite_path: {tmp_path / 'x.sqlite'}\n")
    result = CliRunner().invoke(app, ["maker-report", "-c", str(cfg)])
    assert result.exit_code == 0, result.output
    assert "nothing posted yet" in result.output
