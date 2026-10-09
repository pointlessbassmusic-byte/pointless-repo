"""Polymarket US client against recorded payload shapes (gateway reads were
verified live on 2026-10-09; trading shapes come from the API reference).
Every venue call goes through an httpx MockTransport, so nothing here
touches the network."""

import base64
import json

import httpx
import pytest

from sportsbot.bot.executor import Executor
from sportsbot.core.types import (
    BetIntent,
    BookLevel,
    Exchange,
    MarketInfo,
    MarketQuote,
    Order,
    OrderStatus,
    OrderType,
    Side,
    Sport,
)
from sportsbot.data.store import Store
from sportsbot.exchanges.polymarket_us import (
    PolymarketUSClient,
    polymarket_us_order_fee,
    polymarket_us_taker_fee,
)

MLB_EVENT = {
    "id": "148485", "slug": "mlb-cle-cws-2026-10-08", "title": "Game 4: CLE Guardians vs. CHI White Sox",
    "startTime": "2026-10-09T00:00:00Z", "live": False, "ended": False, "closed": False,
    "teams": [{"id": 3007, "name": "Cleveland Guardians"}, {"id": 3009, "name": "Chicago White Sox"}],
    "tags": [{"slug": "sports"}, {"slug": "mlb", "league": {"slug": "mlb", "ordering": "away"}},
             {"slug": "baseball"}],
    "markets": [
        {"slug": "aec-mlb-cle-cws-2026-10-08", "sportsMarketType": "baseball_team_full_game_winner",
         "status": "MARKET_STATUS_OPEN", "orderPriceMinTickSize": 0.005, "minimumTradeQty": 0.01,
         "feeCoefficient": 0.0695, "endDate": "2026-10-23T04:00:00Z",
         "marketSides": [{"description": "Cleveland Guardians", "long": True, "teamId": 3007},
                         {"description": "Chicago White Sox", "long": False, "teamId": 3009}]},
        {"slug": "asc-mlb-cle-cws-2026-10-08-neg-1pt5", "sportsMarketType": "baseball_team_full_game_spread",
         "status": "MARKET_STATUS_OPEN", "line": -1.5,
         "marketSides": [{"description": "-1.50", "long": True}, {"description": "+1.50", "long": False}]},
    ],
}
LIVE_EVENT = dict(MLB_EVENT, slug="mlb-live", live=True,
                  markets=[dict(MLB_EVENT["markets"][0], slug="aec-mlb-live")])
BOOK = {"marketData": {"marketSlug": "aec-mlb-cle-cws-2026-10-08", "state": "MARKET_STATE_OPEN",
                       "bids": [{"px": {"value": "0.4800", "currency": "USD"}, "qty": "120.5"},
                                {"px": {"value": "0.5000", "currency": "USD"}, "qty": "10"}],
                       "offers": [{"px": {"value": "0.5300", "currency": "USD"}, "qty": "7"},
                                  {"px": {"value": "0.5200", "currency": "USD"}, "qty": "40"}]}}


def _secret():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = ed25519.Ed25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    # the portal hands out a 64-byte secret: private seed + public key
    return base64.b64encode(priv + pub).decode(), key.public_key()


class _Venue:
    """Scripted venue: routes by (method, path); records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        key = (request.method, request.url.path)
        handler = self.routes.get(key)
        if handler is None:
            return httpx.Response(404, json={"message": "no route"})
        if callable(handler):
            return handler(request)
        status, body = handler
        return httpx.Response(status, json=body)


def _client(routes, secret=None, **kw):
    venue = _Venue(routes)
    client = PolymarketUSClient(key_id="11111111-2222-3333-4444-555555555555",
                                secret_key=secret, transport=httpx.MockTransport(venue),
                                max_rps=0, **kw)
    return client, venue


# ---------------------------------------------------------------- reads
def test_discovery_maps_winner_markets_and_skips_live_and_spreads():
    client, _ = _client({("GET", "/v2/sports/baseball/events"): (200, {"events": [MLB_EVENT, LIVE_EVENT]})})
    ms = client.list_sports_markets("baseball")
    assert [m.market_id for m in ms] == ["aec-mlb-cle-cws-2026-10-08"]   # spread + live dropped
    m = ms[0]
    assert m.exchange == Exchange.POLYMARKET_US and m.sport == Sport.BASEBALL
    assert (m.home, m.away) == ("Cleveland Guardians", "Chicago White Sox")   # long side = YES
    assert m.meta["home_field"] == "Chicago White Sox"                       # MLB lists away first
    assert m.start_time.isoformat() == "2026-10-09T00:00:00+00:00"
    assert m.tick_size == 0.005 and m.min_order_size == 0.01
    assert m.meta["league"] == "mlb"

    with_live, _ = _client({("GET", "/v2/sports/baseball/events"): (200, {"events": [LIVE_EVENT]})},
                           include_live=True)
    assert with_live.list_sports_markets("baseball")[0].meta["live"] is True


def test_book_is_long_terms_sorted_both_ways():
    client, _ = _client({("GET", "/v1/markets/aec-mlb-cle-cws-2026-10-08/book"): (200, BOOK)})
    q = client.get_quote(MarketInfo(exchange=Exchange.POLYMARKET_US, market_id="aec-mlb-cle-cws-2026-10-08"))
    assert (q.bid, q.ask) == (0.50, 0.52)
    assert [lvl.price for lvl in q.bids] == [0.50, 0.48] and [lvl.size for lvl in q.asks] == [40.0, 7.0]


def test_settlement_is_the_long_side_payout():
    client, _ = _client({("GET", "/v1/markets/won/settlement"): (200, {"slug": "won", "settlement": 1}),
                         ("GET", "/v1/markets/lost/settlement"): (200, {"slug": "lost", "settlement": 0})})
    assert client.get_resolution("won") is True
    assert client.get_resolution("lost") is False
    assert client.get_resolution("open") is None                 # 404: not settled


# ----------------------------------------------------------------- fees
def test_fee_formulas_match_the_documented_examples():
    assert polymarket_us_order_fee(0.10, 1000) == 6.26
    assert polymarket_us_order_fee(0.50, 1000) == 17.38
    assert polymarket_us_order_fee(0.10, 1000, theta=-0.0125) == -1.12
    assert polymarket_us_order_fee(0.50, 1000, theta=-0.0125) == -3.12
    assert abs(polymarket_us_taker_fee(0.5, 1.0) - 0.017375) < 1e-9       # linear marginal


# ------------------------------------------------------------- signing
def test_requests_are_ed25519_signed_over_timestamp_method_path():
    secret, pub = _secret()
    seen = {}

    def capture(request):
        seen.update(request.headers)
        return httpx.Response(200, json={"balances": [{"currency": "USD", "buyingPower": 150.25}]})

    client, _ = _client({("GET", "/v1/account/balances"): capture}, secret=secret)
    assert client.get_balance() == 150.25
    msg = f"{seen['x-pm-timestamp']}GET/v1/account/balances".encode()
    pub.verify(base64.b64decode(seen["x-pm-signature"]), msg)         # raises if wrong
    assert seen["x-pm-access-key"] == client.key_id


def test_trading_without_keys_fails_closed_but_reads_work():
    client, _ = _client({("GET", "/v1/markets/x/settlement"): (200, {"settlement": 1})})
    assert client.get_resolution("x") is True
    order = client.place_order(Order(market_id="x", side=Side.YES, price=0.5, size=1))
    assert order.status == OrderStatus.REJECTED and "not set" in order.raw["error"]


# -------------------------------------------------------------- orders
def test_place_order_maps_side_post_only_and_reads_back_state():
    secret, _ = _secret()
    order_state = {"state": "ORDER_STATE_PARTIALLY_FILLED", "cumQuantity": 2, "quantity": 5,
                   "id": "o1", "marketSlug": "m", "intent": "ORDER_INTENT_BUY_SHORT",
                   "price": {"value": "0.4100", "currency": "USD"}}
    client, venue = _client({("POST", "/v1/orders"): (200, {"id": "o1"}),
                             ("GET", "/v1/order/o1"): (200, {"order": order_state})}, secret=secret)
    order = client.place_order(Order(market_id="m", side=Side.NO, price=0.41, size=5,
                                     order_type=OrderType.LIMIT, post_only=True))
    body = json.loads(venue.calls[0].content)
    assert body["intent"] == "ORDER_INTENT_BUY_SHORT" and body["participateDontInitiate"] is True
    assert body["price"] == {"value": "0.4100", "currency": "USD"} and body["tif"].endswith("GOOD_TILL_CANCEL")
    assert body["manualOrderIndicator"] == "MANUAL_ORDER_INDICATOR_AUTOMATIC"
    assert order.order_id == "o1" and order.status == OrderStatus.PARTIAL and order.filled == 2.0
    assert order.side == Side.NO


def test_rejected_or_rate_limited_orders_are_never_resubmitted():
    secret, _ = _secret()
    client, venue = _client({("POST", "/v1/orders"): (429, {"status": 429, "message": "Too Many Requests"})},
                            secret=secret)
    order = client.place_order(Order(market_id="m", side=Side.YES, price=0.5, size=1))
    assert order.status == OrderStatus.REJECTED
    assert len(venue.calls) == 1                                        # one POST, no retry

    client, venue = _client({("POST", "/v1/orders"): (400, {"message": "Global Rate Limit Exceeded"})},
                            secret=secret)
    order = client.place_order(Order(market_id="m", side=Side.YES, price=0.5, size=1))
    assert order.status == OrderStatus.REJECTED and "Global Rate Limit" in order.raw["error"]
    assert len(venue.calls) == 1


def test_open_orders_get_order_and_cancel():
    secret, _ = _secret()
    rows = [{"id": "a", "marketSlug": "m", "intent": "ORDER_INTENT_BUY_LONG", "state": "ORDER_STATE_NEW",
             "quantity": 3, "cumQuantity": 0, "price": {"value": "0.5"}},
            {"id": "b", "marketSlug": "m", "outcomeSide": "OUTCOME_SIDE_NO", "state": "ORDER_STATE_FILLED",
             "quantity": 3, "cumQuantity": 3, "price": {"value": "0.4"}}]
    client, venue = _client({("GET", "/v1/orders/open"): (200, {"orders": rows}),
                             ("GET", "/v1/order/b"): (200, {"order": rows[1]}),
                             ("POST", "/v1/order/a/cancel"): (200, {})}, secret=secret)
    opened = client.get_open_orders()
    assert [(o.order_id, o.side, o.status) for o in opened] == [
        ("a", Side.YES, OrderStatus.OPEN), ("b", Side.NO, OrderStatus.FILLED)]
    assert client.get_order("b").filled == 3.0
    assert client.cancel_order("a") is True
    assert json.loads(venue.calls[-1].content) == {}


def test_positions_and_close_position():
    secret, _ = _secret()
    positions = {"positions": {"m": {"netPositionDecimal": "4.0", "cost": {"value": "1.80"},
                                     "realized": {"value": "0"}, "marketMetadata": {"slug": "m"}},
                               "gone": {"netPositionDecimal": "2", "expired": True, "cost": {"value": "1"}}},
                 "eof": True}
    closed = {"id": "c1", "marketSlug": "m", "intent": "ORDER_INTENT_SELL_LONG", "state": "ORDER_STATE_FILLED",
              "quantity": 4, "cumQuantity": 4, "price": {"value": "0.60"}, "avgPx": {"value": "0.61"},
              "commissionNotionalTotalCollected": {"value": "0.07"}}
    client, venue = _client({("GET", "/v1/portfolio/positions"): (200, positions),
                             ("POST", "/v1/order/close-position"): (200, {"id": "c1"}),
                             ("GET", "/v1/order/c1"): (200, {"order": closed})}, secret=secret)
    pos = client.get_positions()
    assert len(pos) == 1 and pos[0].side == Side.YES and pos[0].size == 4.0 and pos[0].avg_price == 0.45

    thin = MarketQuote(market_id="m", bid=0.01, ask=0.05, bids=[BookLevel(price=0.01, size=9)], asks=[])
    assert client.close_position("m", Side.YES, thin, min_price=0.02) is None   # never dump into nothing
    assert not any(c.url.path.endswith("close-position") for c in venue.calls)

    quote = MarketQuote(market_id="m", bid=0.60, ask=0.62, bids=[BookLevel(price=0.60, size=50)], asks=[])
    result = client.close_position("m", Side.YES, quote)
    assert result["closed_size"] == 4.0 and result["avg_price"] == 0.61 and result["fee"] == 0.07
    assert result["proceeds"] == pytest.approx(4 * 0.61 - 0.07)
    assert json.loads([c for c in venue.calls if c.url.path.endswith("close-position")][0].content)["marketSlug"] == "m"


# ---------------------------------------------------------- integration
def test_maker_intents_become_post_only_orders(tmp_path):
    class _Rec:
        exchange = Exchange.POLYMARKET_US
        last = None

        def place_order(self, order):
            self.last = order
            order.status = OrderStatus.OPEN
            order.order_id = "x"
            return order

        def get_open_orders(self):
            return []

    venue = _Rec()
    ex = Executor(venue, Store(str(tmp_path / "s.sqlite")), mode="live")
    market = MarketInfo(exchange=Exchange.POLYMARKET_US, market_id="m", sport=Sport.TENNIS,
                        home="A", away="B")
    ex.submit(BetIntent(intent_id="i", market=market, side=Side.YES, prob=0.6, price=0.49,
                        size=2, edge=0.05, kelly_fraction=0.01, maker=True))
    assert venue.last.post_only is True
    ex.submit(BetIntent(intent_id="j", market=market, side=Side.YES, prob=0.6, price=0.51,
                        size=2, edge=0.05, kelly_fraction=0.01))
    assert venue.last.post_only is False
