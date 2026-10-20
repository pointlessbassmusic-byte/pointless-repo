"""Polymarket US client: signing, discovery, books, settlement, orders.

Fixtures mirror the gateway's 2026-10-13 responses (field names and the
moneyline / side structure), trimmed."""

import base64
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from sportsbot.core.types import Exchange, MarketInfo, Order, OrderStatus, OrderType, Side, Sport
from sportsbot.exchanges import polymarket_us as pmus
from sportsbot.exchanges.polymarket_us import PolymarketUSClient, sign_headers, taker_fee_us

SEED = bytes(range(32))
SECRET = base64.b64encode(SEED).decode()


def _soon(h=3):
    return (datetime.now(timezone.utc) + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _side(desc, long, safe, ordering):
    return {"description": desc, "long": long, "team": {"safeName": safe, "ordering": ordering}}


def _ml(slug, outcomes, sides, start=None, **kw):
    m = {"slug": slug, "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
         "sportsMarketType": "x_full_game_winner", "active": True, "closed": False,
         "outcomes": json.dumps(outcomes), "marketSides": sides, "gameStartTime": start or _soon(),
         "orderPriceMinTickSize": 0.01, "minimumTradeQty": 0.01, "feeCoefficient": 0.0695,
         "question": " vs. ".join(outcomes), "endDate": _soon(30)}
    m.update(kw)
    return m


def _prop(slug):
    return {"slug": slug, "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_PROP", "active": True,
            "closed": False, "outcomes": '["Yes","No"]'}


class FakeHttp:
    def __init__(self, routes=None, post_status=200, post_body=None):
        self.routes = routes or {}
        self.gets, self.posts = [], []
        self.post_status, self.post_body = post_status, post_body or {}

    def get(self, url, params=None, headers=None):
        self.gets.append((url, params, headers))
        for frag, (status, body) in self.routes.items():
            if frag in url:
                return httpx.Response(status, json=body, request=httpx.Request("GET", url))
        return httpx.Response(404, json={"code": 5}, request=httpx.Request("GET", url))

    def post(self, url, json=None, headers=None):
        self.posts.append((url, json, headers))
        return httpx.Response(self.post_status, json=self.post_body, request=httpx.Request("POST", url))


def test_signature_verifies_against_the_public_key_and_accepts_64_byte_secrets():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    h = sign_headers("key-1", SECRET, "post", "/v1/orders", now_ms=1700000000000)
    assert h["X-PM-Access-Key"] == "key-1" and h["X-PM-Timestamp"] == "1700000000000"
    pub = Ed25519PrivateKey.from_private_bytes(SEED).public_key()
    pub.verify(base64.b64decode(h["X-PM-Signature"]), b"1700000000000POST/v1/orders")   # raises if wrong
    long_secret = base64.b64encode(SEED + bytes(32)).decode()
    assert sign_headers("key-1", long_secret, "POST", "/v1/orders", now_ms=1)["X-PM-Signature"] == \
        sign_headers("key-1", SECRET, "POST", "/v1/orders", now_ms=1)["X-PM-Signature"]


def test_taker_fee_matches_the_published_schedule():
    assert taker_fee_us(0.50, 100) == pytest.approx(1.7375)        # docs: $1.74 on a 100-lot at 0.50
    assert taker_fee_us(0.50, 100, "table_tennis") == pytest.approx(2.50)
    assert taker_fee_us(0.80, 100) == pytest.approx(1.112)


def test_event_moneylines_parse_names_home_field_and_skip_everything_else():
    nhl = {"slug": "nhl-nj-det", "markets": [
        _prop("tsc-nhl-nj-det-tt"),
        _ml("aec-nhl-nj-det", ["Devils", "Red Wings"],
            [_side("Devils", True, "New Jersey Devils", "away"), _side("Red Wings", False, "Detroit Red Wings", "home")]),
        _ml("aec-nhl-closed", ["Devils", "Red Wings"], [], closed=True),
        _ml("aec-nhl-unknown", ["Devils", "Nobodies"], [_side("Devils", True, "", None), _side("Nobodies", False, "", None)]),
    ]}
    ms = PolymarketUSClient.moneylines_from_event(nhl, Sport.HOCKEY, "nhl", "away")
    assert [m.market_id for m in ms] == ["aec-nhl-nj-det"]          # prop, closed and unmatchable skipped
    m = ms[0]
    assert m.exchange is Exchange.POLYMARKET_US and m.sport is Sport.HOCKEY
    assert m.home and m.away and m.home != m.away                    # canonical team names
    assert m.min_order_size == 0.01 and m.meta["fee_coefficient"] == 0.0695

    mlb = {"slug": "mlb-cle-tb", "markets": [_ml(
        "aec-mlb-cle-tb", ["Cleveland Guardians", "Tampa Bay Rays"],
        [_side("Cleveland Guardians", True, "Guardians", "away"), _side("Tampa Bay Rays", False, "Rays", "home")])]}
    b = PolymarketUSClient.moneylines_from_event(mlb, Sport.BASEBALL, "mlb", "away")[0]
    assert (b.home, b.away) == ("Cleveland Guardians", "Tampa Bay Rays")      # outcomes[0] is the LONG side
    assert b.meta["home_field"] == "Tampa Bay Rays"                          # the side flagged home

    cfb = {"slug": "cfb-del-mtnst", "markets": [_ml(
        "aec-cfb-del-mtnst", ["Fightin' Blue Hens", "Blue Raiders"],
        [_side("Fightin' Blue Hens", True, "Delaware", "away"), _side("Blue Raiders", False, "Middle Tennessee", "home")])]}
    c = PolymarketUSClient.moneylines_from_event(cfb, Sport.COLLEGE_FOOTBALL, "cfb")[0]
    assert (c.home, c.away) == ("Delaware", "Middle Tennessee")

    stale = {"slug": "x", "markets": [_ml("aec-atp-old", ["A", "B"], [], start=(
        datetime.now(timezone.utc) - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%SZ"))]}
    assert PolymarketUSClient.moneylines_from_event(stale, Sport.TENNIS, "atp") == []


def test_list_sports_markets_walks_each_league_and_dedupes(monkeypatch):
    ev = {"slug": "atp-a-b", "markets": [_ml("aec-atp-a-b", ["Ann A", "Bea B"], [])]}
    http = FakeHttp({"/v1/sports": (200, {"sports": [
        {"sport": "atp", "series": "16", "isOperational": True},
        {"sport": "wta", "series": "17", "isOperational": True}]}),
        "/v1/events": (200, {"events": [ev]})})
    c = PolymarketUSClient(key_id="", secret_key="", http=http)
    ms = c.list_sports_markets("tennis")
    assert [m.market_id for m in ms] == ["aec-atp-a-b"]
    assert {g[1]["seriesId"] for g in http.gets if "/v1/events" in g[0]} == {"16", "17"}
    assert all(g[2] == {} for g in http.gets)                       # public: no auth headers
    n = len(http.gets)
    assert c.list_sports_markets("soccer") == [] and len(http.gets) == n   # unsupported: no call
    with pytest.raises(ValueError):
        c.list_sports_markets("curling")


def test_book_and_settlement_parsing():
    book = {"marketData": {"bids": [{"px": {"value": "0.4800"}, "qty": "152543.74"},
                                    {"px": {"value": "0.4900"}, "qty": "30653.82"},
                                    {"px": {"value": "0.4700"}, "qty": "0"}],
                           "offers": [{"px": {"value": "0.5100"}, "qty": "123285.73"},
                                      {"px": {"value": "0.5000"}, "qty": "70326.93"}]}}
    http = FakeHttp({"/book": (200, book), "settled/settlement": (200, {"slug": "settled", "settlement": 1}),
                     "lost/settlement": (200, {"slug": "lost", "settlement": 0})})
    c = PolymarketUSClient(key_id="", secret_key="", http=http)
    q = c.get_quote(MarketInfo(exchange=Exchange.POLYMARKET_US, market_id="aec-nhl-nj-det"))
    assert (q.bid, q.ask) == (0.49, 0.50) and q.bids[0].size == pytest.approx(30653.82)
    assert [b.price for b in q.bids] == [0.49, 0.48] and [a.price for a in q.asks] == [0.50, 0.51]
    assert c.get_resolution("settled") is True and c.get_resolution("lost") is False
    assert c.get_resolution("open-market") is None                  # 404 "Settlement not found"


def test_bbo_reads_the_running_trade_counter_and_last_print():
    bbo = {"marketData": {"marketSlug": "aec-nhl-nj-det", "currentPx": {"value": "0.4950"},
                          "lastTradePx": {"value": "0.4900", "currency": "USD"},
                          "sharesTraded": "18882.8300", "openInterest": "13832.1400",
                          "bestAsk": {"value": "0.5000"}, "bestBid": {"value": "0.4900"},
                          "lastPriceSample": {"ts": "2026-10-13T16:17:25.038731659Z"},
                          "state": "MARKET_STATE_OPEN"}}
    c = PolymarketUSClient(key_id="", secret_key="", http=FakeHttp({"/bbo": (200, bbo)}))
    b = c.get_bbo("aec-nhl-nj-det")
    assert (b["bid"], b["ask"], b["last_px"]) == (0.49, 0.50, 0.49)
    assert b["traded"] == pytest.approx(18882.83) and b["open_interest"] == pytest.approx(13832.14)
    assert b["last_ts"].tzinfo is not None and b["state"] == "MARKET_STATE_OPEN"
    empty = PolymarketUSClient(key_id="", secret_key="", http=FakeHttp({"/bbo": (200, {"marketData": {}})}))
    assert empty.get_bbo("x")["traded"] is None and empty.get_bbo("x")["last_px"] is None


def test_order_body_prices_in_yes_terms_and_sends_the_maker_only_flag():
    c = PolymarketUSClient(key_id="k", secret_key=SECRET, http=FakeHttp())
    yes = Order(market_id="aec-nhl-nj-det", side=Side.YES, price=0.49, size=2.0408, post_only=True)
    b = c.order_body(yes)
    assert b["intent"] == "ORDER_INTENT_BUY_LONG" and b["price"] == {"value": "0.4900", "currency": "USD"}
    assert b["participateDontInitiate"] is True and b["tif"] == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
    assert b["quantity"] == 2.04 and b["marketSlug"] == "aec-nhl-nj-det"
    assert b["manualOrderIndicator"] == "MANUAL_ORDER_INDICATOR_AUTOMATIC"
    no = Order(market_id="aec-nhl-nj-det", side=Side.NO, price=0.50, size=2.0, order_type=OrderType.IOC)
    nb = c.order_body(no)
    assert nb["intent"] == "ORDER_INTENT_BUY_SHORT" and nb["price"]["value"] == "0.5000"   # 1 - 0.50
    assert nb["participateDontInitiate"] is False and nb["tif"] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
    assert c.order_body(Order(market_id="m", side=Side.NO, price=0.53, size=1))["price"]["value"] == "0.4700"


def test_place_order_needs_keys_posts_once_and_reads_executions():
    http = FakeHttp()
    c = PolymarketUSClient(key_id="", secret_key="", http=http)
    o = c.place_order(Order(market_id="m", side=Side.YES, price=0.49, size=2))
    assert o.status == OrderStatus.REJECTED and http.posts == []

    rest = FakeHttp(post_body={"id": "ord-1", "executions": [{"type": "EXECUTION_TYPE_NEW"}]})
    c = PolymarketUSClient(key_id="k", secret_key=SECRET, http=rest)
    o = c.place_order(Order(market_id="aec-x", side=Side.YES, price=0.49, size=2, post_only=True))
    assert o.status == OrderStatus.OPEN and o.order_id == "ord-1" and len(rest.posts) == 1
    url, body, headers = rest.posts[0]
    assert url.endswith("/v1/orders") and body["participateDontInitiate"] is True
    assert {"X-PM-Access-Key", "X-PM-Timestamp", "X-PM-Signature"} <= set(headers)

    part = FakeHttp(post_body={"id": "ord-2", "executions": [
        {"type": "EXECUTION_TYPE_PARTIAL_FILL", "lastShares": "1.5"}]})
    o = PolymarketUSClient(key_id="k", secret_key=SECRET, http=part).place_order(
        Order(market_id="aec-x", side=Side.YES, price=0.49, size=2))
    assert o.status == OrderStatus.PARTIAL and o.filled == 1.5

    rej = FakeHttp(post_body={"id": "ord-3", "executions": [
        {"type": "EXECUTION_TYPE_REJECTED", "orderRejectReason": "would cross (participateDontInitiate)"}]})
    o = PolymarketUSClient(key_id="k", secret_key=SECRET, http=rej).place_order(
        Order(market_id="aec-x", side=Side.YES, price=0.50, size=2, post_only=True))
    assert o.status == OrderStatus.REJECTED and "cross" in o.raw["reject_reason"]

    boom = FakeHttp(post_status=503)
    o = PolymarketUSClient(key_id="k", secret_key=SECRET, http=boom).place_order(
        Order(market_id="aec-x", side=Side.YES, price=0.49, size=2))
    assert o.status == OrderStatus.REJECTED and len(boom.posts) == 1          # never retried


def test_cancel_uses_the_remembered_slug_or_looks_it_up():
    http = FakeHttp(post_body={"id": "ord-1", "executions": []},
                    routes={"/v1/order/ord-9": (200, {"order": {"id": "ord-9", "marketSlug": "aec-y"}})})
    c = PolymarketUSClient(key_id="k", secret_key=SECRET, http=http)
    c.place_order(Order(market_id="aec-x", side=Side.YES, price=0.49, size=2))
    assert c.cancel_order("ord-1") is True
    assert http.posts[-1][0].endswith("/v1/order/ord-1/cancel") and http.posts[-1][1] == {"marketSlug": "aec-x"}
    assert c.cancel_order("ord-9") is True and http.posts[-1][1] == {"marketSlug": "aec-y"}
    assert PolymarketUSClient(key_id="", secret_key="", http=FakeHttp()).cancel_order("ord-1") is False


def test_venue_orders_and_positions_map_to_engine_terms():
    o = PolymarketUSClient.order_from_venue({"id": "a", "marketSlug": "aec-x", "intent": "ORDER_INTENT_BUY_SHORT",
                                             "price": {"value": "0.4700"}, "quantity": 2, "cumQuantity": 1,
                                             "state": "ORDER_STATE_PARTIALLY_FILLED"})
    assert o.side == Side.NO and o.price == pytest.approx(0.53) and o.status == OrderStatus.PARTIAL
    http = FakeHttp({"/v1/portfolio/positions": (200, {"positions": {
        "aec-x": {"netPosition": "-3", "avgPx": {"value": "0.47"}},
        "aec-y": {"netPosition": "2", "avgPx": {"value": "0.40"}},
        "aec-z": {"netPosition": "0"}}}),
        "/v1/account/balances": (200, {"balances": [{"currency": "USD", "currentBalance": 50.0, "buyingPower": 42.5}]})})
    c = PolymarketUSClient(key_id="k", secret_key=SECRET, http=http)
    pos = {p.market_id: p for p in c.get_positions()}
    assert pos["aec-x"].side == Side.NO and pos["aec-x"].size == 3 and pos["aec-x"].avg_price == pytest.approx(0.53)
    assert pos["aec-y"].side == Side.YES and "aec-z" not in pos
    assert c.get_balance() == 42.5
    assert all(g[2].get("X-PM-Signature") for g in http.gets)       # authenticated reads are signed


def test_build_exchange_wires_the_venue_in_paper_with_its_fees(monkeypatch, tmp_path):
    from sportsbot.bot.runner import build_exchange
    from sportsbot.exchanges.paper import PaperExchange

    monkeypatch.delenv("SPORTSBOT_LIVE", raising=False)
    ex, data, fee_fn = build_exchange({"exchange": "polymarket_us", "mode": "live",
                                       "bankroll": {"amount": 100.0}})
    assert isinstance(ex, PaperExchange) and isinstance(data, PolymarketUSClient)  # live config, no env: paper
    assert fee_fn(0.5, 100) == pytest.approx(1.7375)
    assert ex.maker_fee_fn(0.5, 100) == 0.0
    from sportsbot.exchanges.base import TapeGap

    monkeypatch.delenv("SPORTSBOT_US_TAPE_DB", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(TapeGap):                     # no keyless tape, none recorded: a gap, never []
        data.recent_trades(MarketInfo(exchange=Exchange.POLYMARKET_US, market_id="m"), datetime.now(timezone.utc))


def test_executor_marks_maker_arms_post_only(tmp_path):
    from sportsbot.bot.executor import Executor
    from sportsbot.core.types import BetIntent
    from sportsbot.data.store import Store
    from sportsbot.exchanges.paper import PaperExchange

    ex = Executor(PaperExchange(starting_balance=100.0), Store(str(tmp_path / "t.sqlite")), mode="paper")
    m = MarketInfo(exchange=Exchange.POLYMARKET_US, market_id="aec-x", sport=Sport.HOCKEY,
                   home="A", away="B", start_time=datetime.now(timezone.utc) + timedelta(hours=3))
    maker = ex.submit(BetIntent(market=m, side=Side.YES, prob=0.5, price=0.40, size=2, edge=0.01,
                                kelly_fraction=0.0, reason="touch join bid=0.40 maker"))
    taker = ex.submit(BetIntent(market=m, side=Side.YES, prob=0.6, price=0.40, size=2, edge=0.05,
                                kelly_fraction=0.0, reason="edge +0.05 taker"))
    assert maker.post_only is True and taker.post_only is False


def test_doctor_knows_the_venue(monkeypatch):
    from sportsbot.bot.doctor import check_mode, check_secrets, fee_floor

    monkeypatch.delenv(pmus.ENV_KEY_ID, raising=False)
    cfg = {"exchange": "polymarket_us", "mode": "paper", "execution": {"slippage_buffer": 0.005}}
    assert any(c.name == "config.exchange" and c.level == "PASS" for c in check_mode(cfg))
    sec = {c.name: c.level for c in check_secrets(cfg)}
    assert sec["secrets.POLYMARKET_US_KEY_ID"] == "WARN"
    assert fee_floor(cfg) == pytest.approx(0.0695 * 0.25 + 0.005)
