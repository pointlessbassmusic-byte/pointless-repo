"""Venue-enforced order lifetime and post-only (EDGE_VERDICT Result 21): a
crashed process must not leave a quote resting into the game, and a maker
quote must never cross and pay the taker fee."""
from datetime import datetime, timedelta, timezone

from sportsbot.bot.executor import VENUE_MIN_EXPIRY_S, Executor, venue_expiry
from sportsbot.core.types import (
    BetIntent, Exchange, MarketInfo, Order, OrderStatus, OrderType, Side, Sport,
)
from sportsbot.data.store import Store
from sportsbot.exchanges.kalshi import KalshiClient
from sportsbot.exchanges.polymarket import PolymarketClient


class FakeVenue:
    exchange = Exchange.POLYMARKET

    def __init__(self):
        self.placed = []

    def place_order(self, order):
        self.placed.append(order)
        order.order_id = "v1"
        order.status = OrderStatus.OPEN
        return order


def _intent(start_in, reason="sharp p=0.6 maker"):
    m = MarketInfo(exchange=Exchange.POLYMARKET, market_id="m1", yes_token_id="t1",
                   no_token_id="t2", question="A vs B", slug="a-b", sport=Sport.BASEBALL,
                   start_time=datetime.now(timezone.utc) + start_in)
    return BetIntent(market=m, side=Side.YES, prob=0.6, price=0.5, size=4.0,
                     edge=0.05, kelly_fraction=0.0, reason=reason)


def test_every_order_carries_a_venue_expiry_past_the_ttl(tmp_path):
    venue = FakeVenue()
    ex = Executor(venue, Store(str(tmp_path / "s.sqlite")), order_ttl_seconds=120)
    before = datetime.now(timezone.utc)
    o = ex.submit(_intent(timedelta(hours=2)))
    assert venue.placed == [o] and o.order_type is OrderType.LIMIT_GTD
    life = (o.expires_at - before).total_seconds()
    assert VENUE_MIN_EXPIRY_S <= life < VENUE_MIN_EXPIRY_S + 5   # >= Polymarket's 3-minute floor
    assert o.post_only is True
    # a long TTL pushes the expiry past the floor, never under it
    t0 = datetime(2026, 10, 13, tzinfo=timezone.utc)
    assert venue_expiry(t0, 600) == t0 + timedelta(seconds=660)


def test_taker_reasons_are_not_post_only(tmp_path):
    ex = Executor(FakeVenue(), Store(str(tmp_path / "s.sqlite")))
    assert ex.submit(_intent(timedelta(hours=2), reason="model p=0.6 taker")).post_only is False


def test_an_order_the_venue_could_not_expire_before_start_is_refused(tmp_path):
    venue = FakeVenue()
    ex = Executor(venue, Store(str(tmp_path / "s.sqlite")), order_ttl_seconds=120)
    o = ex.submit(_intent(timedelta(minutes=4)))
    assert o.status is OrderStatus.REJECTED and venue.placed == []
    assert "outlive" in o.raw["error"]
    assert ex._open == {}


def _order(**kw):
    return Order(exchange=Exchange.POLYMARKET, market_id="m1", token_id="t1",
                 side=Side.YES, order_type=OrderType.LIMIT_GTD, price=0.5, size=4.0,
                 expires_at=datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc), **kw)


def test_polymarket_passes_expiration_and_post_only_to_the_sdk():
    calls = []

    class SDK:
        def place_limit_order(self, **kw):
            calls.append(kw)
            return {"orderID": "x", "status": "live"}

    c = PolymarketClient(private_key="k")
    c._sdk = SDK()
    c.place_order(_order(post_only=True))
    assert calls[0]["expiration"] == int(datetime(2026, 10, 13, 12, tzinfo=timezone.utc).timestamp())
    assert calls[0]["post_only"] is True
    c.place_order(Order(exchange=Exchange.POLYMARKET, market_id="m1", token_id="t1",
                        price=0.5, size=4.0))
    assert "expiration" not in calls[1] and "post_only" not in calls[1]


def test_kalshi_body_carries_expiration_time_and_post_only():
    bodies = []

    class C(KalshiClient):
        def _request_once(self, method, path, json_body=None, **kw):
            bodies.append(json_body)
            return {"order": {"order_id": "k1", "fill_count": "0"}}

    C(env="prod").place_order(_order(post_only=True))
    b = bodies[0]
    assert b["time_in_force"] == "good_till_canceled"
    assert b["expiration_time"] == int(datetime(2026, 10, 13, 12, tzinfo=timezone.utc).timestamp())
    assert b["post_only"] is True
