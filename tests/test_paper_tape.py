"""Tape-verified paper maker fills.

Paper resting orders used to never fill, so the paper book could not
produce maker evidence at all and the rank 2 experiment (maker-only
quoting) had no path to a gate. A resting order now fills only when the
venue's PUBLIC taker tape prints through its limit, for the size that
printed, paying the maker fee; nothing else is assumed.
"""

from datetime import datetime, timedelta, timezone

import pytest

from sportsbot.bot.executor import Executor
from sportsbot.core.books import print_crosses, tape_fill_size
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
    TapePrint,
)
from sportsbot.data.store import Store
from sportsbot.exchanges.base import ExchangeClient
from sportsbot.exchanges.paper import PaperExchange

T0 = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)


def _pr(offset_s, price, size=10.0):
    return TapePrint(ts=T0 + timedelta(seconds=offset_s), price_yes=price, size=size)


class Tape(ExchangeClient):
    """Data client stub: a fixed tape, counts reads per market."""
    exchange = Exchange.POLYMARKET

    def __init__(self, prints):
        self.prints = prints
        self.reads = []

    def list_sports_markets(self, sport_tag):
        return []

    def get_quote(self, market):
        raise RuntimeError

    def place_order(self, order):
        raise RuntimeError

    def cancel_order(self, order_id):
        return False

    def get_open_orders(self):
        return []

    def get_positions(self):
        return []

    def get_balance(self):
        return 0.0

    def recent_trades(self, market, since):
        self.reads.append((market.market_id, since))
        return [p for p in self.prints if p.ts >= since]


def _market(**kw):
    d = dict(exchange=Exchange.POLYMARKET, market_id="m1", yes_token_id="t1",
             no_token_id="t2", slug="a-b", sport=Sport.TENNIS, home="A", away="B",
             start_time=T0 + timedelta(hours=6))
    d.update(kw)
    return MarketInfo(**d)


def _quote(bid=0.44, ask=0.47, depth=200.0):
    return MarketQuote(market_id="m1", bid=bid, ask=ask,
                       bids=[BookLevel(price=bid, size=depth)],
                       asks=[BookLevel(price=ask, size=depth)])


def _order(side=Side.YES, price=0.45, size=100.0):
    return Order(market_id="m1", token_id="t1" if side == Side.YES else "t2", side=side,
                 order_type=OrderType.LIMIT, price=price, size=size)


def test_print_crosses_is_the_one_rule_for_both_frames():
    assert print_crosses(Side.YES, 0.45, 0.45) and print_crosses(Side.YES, 0.45, 0.40)
    assert not print_crosses(Side.YES, 0.45, 0.46)
    # a NO bid at 0.40 is a YES ask at 0.60
    assert print_crosses(Side.NO, 0.40, 0.60) and print_crosses(Side.NO, 0.40, 0.65)
    assert not print_crosses(Side.NO, 0.40, 0.59)
    # the backtest's rule is the same function
    from sportsbot.backtest.polymarket_market import maker_fill_printed
    assert maker_fill_printed([(10, 0.45)], after_ts=0, start_ts=100, side="YES", limit=0.45)
    assert not maker_fill_printed([(10, 0.46)], after_ts=0, start_ts=100, side="YES", limit=0.45)
    assert maker_fill_printed([(10, 0.60)], after_ts=0, start_ts=100, side="NO", limit=0.40)


def test_tape_fill_size_counts_only_crossing_prints_in_the_window():
    prints = [_pr(-5, 0.40, 50), _pr(5, 0.46, 50), _pr(10, 0.45, 30), _pr(20, 0.44, 25),
              _pr(90, 0.30, 500)]
    assert tape_fill_size(prints, Side.YES, 0.45, after=T0) == pytest.approx(555.0)
    assert tape_fill_size(prints, Side.YES, 0.45, after=T0,
                          before=T0 + timedelta(seconds=60)) == pytest.approx(55.0)
    # a NO bid at 0.56 is a YES ask at 0.44: the 0.46/0.45/0.44 prints all cross it
    assert tape_fill_size(prints, Side.NO, 0.56, after=T0) == pytest.approx(105.0)
    assert tape_fill_size(prints, Side.NO, 0.50, after=T0) == pytest.approx(0.0)    # YES ask 0.50


class TestPaperReconcile:
    def test_resting_yes_bid_fills_only_when_a_later_print_goes_through_it(self):
        tape = Tape([_pr(-10, 0.44, 80),            # before placement: ignored
                     _pr(5, 0.46, 80),              # above our bid: ignored
                     _pr(10, 0.45, 30)])            # through: 30 shares
        paper = PaperExchange(data_client=tape, starting_balance=100.0,
                              maker_fee_fn=lambda p, n, m=None: 0.01 * n)
        o = paper.place_order(_order(price=0.45, size=100.0), quote=_quote(ask=0.47),
                              market=_market(), now=T0)
        assert o.status == OrderStatus.OPEN and o.filled == 0.0
        changed = paper.reconcile_resting(now=T0 + timedelta(seconds=60))
        assert [c.order_id for c in changed] == [o.order_id]
        assert o.filled == pytest.approx(30.0) and o.status == OrderStatus.PARTIAL
        assert paper.fills[-1].price == 0.45 and paper.fills[-1].size == 30.0
        assert paper.fills[-1].fee == pytest.approx(0.30)
        assert paper.get_balance() == pytest.approx(100.0 - 30 * 0.45 - 0.30)
        assert paper.positions[("m1", Side.YES)].size == 30.0
        # same tape again: nothing new, no double fill
        assert paper.reconcile_resting(now=T0 + timedelta(seconds=120)) == []
        assert o.filled == pytest.approx(30.0)
        # a big print finishes it; the order leaves the book
        tape.prints.append(_pr(70, 0.43, 500))
        (done,) = paper.reconcile_resting(now=T0 + timedelta(seconds=180))
        assert done.status == OrderStatus.FILLED and done.filled == 100.0
        assert paper.get_open_orders() == [] and done.raw["tape_filled"] == 100.0
        assert tape.reads[0] == ("m1", T0)

    def test_no_bid_fills_on_a_yes_print_at_its_complement(self):
        tape = Tape([_pr(5, 0.59, 40), _pr(6, 0.61, 40)])
        paper = PaperExchange(data_client=tape, starting_balance=100.0)
        o = paper.place_order(_order(side=Side.NO, price=0.40, size=50.0),
                              quote=_quote(bid=0.58, ask=0.62), market=_market(), now=T0)
        assert o.filled == 0.0                       # NO costs 1-0.58=0.42 > 0.40: rests
        paper.reconcile_resting(now=T0 + timedelta(seconds=30))
        assert o.filled == pytest.approx(40.0) and paper.fills[-1].price == 0.40
        assert paper.positions[("m1", Side.NO)].avg_price == pytest.approx(0.40)

    def test_partial_book_fill_then_tape_fills_only_the_remainder(self):
        tape = Tape([_pr(5, 0.45, 1000)])
        paper = PaperExchange(data_client=tape, starting_balance=1000.0)
        o = paper.place_order(_order(price=0.45, size=100.0), quote=_quote(ask=0.45, depth=60.0),
                              market=_market(), now=T0)
        assert o.filled == 60.0 and o.status == OrderStatus.PARTIAL
        paper.reconcile_resting(now=T0 + timedelta(seconds=30))
        assert o.filled == 100.0 and o.status == OrderStatus.FILLED
        assert sum(f.size for f in paper.fills) == pytest.approx(100.0)

    def test_no_data_client_or_empty_tape_never_fills_and_cancel_clears(self):
        paper = PaperExchange(starting_balance=100.0)
        o = paper.place_order(_order(), quote=_quote(ask=0.47), market=_market(), now=T0)
        assert paper.reconcile_resting() == [] and o.filled == 0.0
        tape = Tape([])
        paper2 = PaperExchange(data_client=tape, starting_balance=100.0)
        o2 = paper2.place_order(_order(), quote=_quote(ask=0.47), market=_market(), now=T0)
        assert paper2.reconcile_resting() == [] and o2.status == OrderStatus.OPEN
        assert paper2.cancel_order(o2.order_id) and paper2._resting == {}
        tape.prints.append(_pr(5, 0.40, 100))
        assert paper2.reconcile_resting() == []       # cancelled: no fill after the fact

    def test_insufficient_balance_skips_the_tape_fill(self):
        tape = Tape([_pr(5, 0.45, 100)])
        paper = PaperExchange(data_client=tape, starting_balance=10.0)
        o = paper.place_order(_order(price=0.45, size=100.0), quote=_quote(ask=0.47),
                              market=_market(), now=T0)
        assert paper.reconcile_resting() == [] and o.filled == 0.0
        assert paper.get_balance() == 10.0

    def test_market_is_rebuilt_from_the_order_when_not_passed(self):
        tape = Tape([_pr(5, 0.45, 10)])
        paper = PaperExchange(data_client=tape, starting_balance=100.0)
        paper.place_order(_order(side=Side.NO, price=0.56), quote=_quote(bid=0.40, ask=0.47), now=T0)
        mk = paper._resting[next(iter(paper._resting))][0]
        assert mk.market_id == "m1" and mk.no_token_id == "t2" and mk.yes_token_id is None
        assert mk.exchange == Exchange.POLYMARKET
        # a tape read failure fills nothing and keeps the order resting
        tape.recent_trades = lambda market, since: (_ for _ in ()).throw(RuntimeError("down"))
        assert paper.reconcile_resting() == [] and len(paper.open_orders) == 1


def test_executor_books_tape_fills_as_maker_bets(tmp_path):
    store = Store(str(tmp_path / "t.sqlite"))
    tape = Tape([_pr(5, 0.45, 40)])
    paper = PaperExchange(data_client=tape, starting_balance=100.0)
    ex = Executor(paper, store, mode="paper")
    intent = BetIntent(market=_market(), side=Side.YES, prob=0.55, price=0.45, size=100.0,
                       edge=0.08, kelly_fraction=0.02, reason="test p=0.550 blend=0.50 mid=0.455 maker")
    paper_now = {"t": T0}
    orig = paper.place_order
    paper.place_order = lambda order, quote=None, market=None, now=None: orig(
        order, quote=quote, market=market, now=paper_now["t"])
    order = ex.submit(intent, quote=_quote(ask=0.47))
    assert order.status == OrderStatus.OPEN and store.exposure_by()["total"] == 0.0
    assert tape.reads == [] or tape.reads[0][0] == "m1"
    ex.reconcile_open_orders()
    exp = store.exposure_by()
    assert exp["total"] == pytest.approx(0.45 * 40.0)
    (bet,) = store.all_bets("paper")
    assert bet["size"] == 40.0 and bet["entry_price"] == 0.45 and bet["arm"] == "tennis/model/maker"
    assert order.client_id in ex._open                 # 60 still resting
    row = store.conn.execute("SELECT status, filled FROM orders WHERE client_id=?",
                             (order.client_id,)).fetchone()
    assert tuple(row) == ("partial", 40.0)
    tape.prints.append(_pr(30, 0.44, 100))
    ex.reconcile_open_orders()
    assert order.client_id not in ex._open and store.exposure_by()["total"] == pytest.approx(45.0)
    assert len(store.all_bets("paper")) == 2


def test_polymarket_tape_flips_the_no_token_frame_and_stops_at_since():
    from sportsbot.exchanges.polymarket import PolymarketClient

    c = PolymarketClient()
    calls = []
    page = [
        {"side": "BUY", "asset": "t2", "size": 10, "price": 0.56, "timestamp": 1000, "transactionHash": "a"},
        {"side": "BUY", "asset": "t1", "size": 5, "price": 0.45, "timestamp": 990, "transactionHash": "b"},
        {"side": "SELL", "asset": "t1", "size": 7, "price": 0.43, "timestamp": 980, "transactionHash": "c"},
        {"side": "BUY", "asset": "t1", "size": 5, "price": 0.45, "timestamp": 990, "transactionHash": "b"},  # dup
        {"side": "BUY", "asset": "zzz", "size": 5, "price": 0.45, "timestamp": 985, "transactionHash": "d"},
        {"side": "BUY", "asset": "t1", "size": 9, "price": 0.50, "timestamp": 900, "transactionHash": "e"},  # before since
    ]
    c._get = lambda url, params=None: (calls.append((url, params)), page)[1]
    since = datetime.fromtimestamp(950, tz=timezone.utc)
    out = c.recent_trades(_market(), since)
    assert [(p.price_yes, p.size, p.taker_side) for p in out] == [
        (0.44, 10.0, "no"), (0.45, 5.0, "yes"), (0.43, 7.0, "no")]
    assert out[0].ts == datetime.fromtimestamp(1000, tz=timezone.utc)
    assert len(calls) == 1 and calls[0][1]["market"] == "m1" and calls[0][1]["takerOnly"] == "true"
    # only the NO token id known: every other asset is YES (binary market),
    # so the unknown-asset print counts too
    out2 = c.recent_trades(_market(yes_token_id=None), since)
    assert [p.price_yes for p in out2] == [0.44, 0.45, 0.45, 0.43]


def test_kalshi_tape_reads_the_2026_string_fields():
    from sportsbot.exchanges.kalshi import KalshiClient

    c = KalshiClient(env="prod")
    calls = []
    payload = {"cursor": "", "trades": [
        {"count_fp": "44.58", "created_time": "2026-10-09T19:46:37.920698Z", "taker_side": "yes",
         "yes_price_dollars": "0.4400", "no_price_dollars": "0.5600", "trade_id": "x1",
         "ticker": "KXMLBGAME-26OCT102000CWSCLE-CWS"},
        {"count_fp": "11.14", "created_time": "2026-10-09T19:45:58.111706Z", "taker_side": "no",
         "yes_price_dollars": "0.4300", "trade_id": "x2"},
        {"count_fp": "1", "created_time": "2026-10-09T18:00:00Z", "yes_price_dollars": "0.40"},  # before since
        {"count_fp": "", "created_time": "2026-10-09T19:46:00Z", "yes_price_dollars": "0.41"},   # unparseable
    ]}
    c._request = lambda method, path, params=None, **kw: (calls.append((method, path, params)), payload)[1]
    since = datetime(2026, 10, 9, 19, 0, tzinfo=timezone.utc)
    out = c.recent_trades(_market(exchange=Exchange.KALSHI, market_id="KXMLBGAME-26OCT102000CWSCLE-CWS"), since)
    assert [(p.price_yes, p.size, p.taker_side, p.trade_id) for p in out] == [
        (0.44, 44.58, "yes", "x1"), (0.43, 11.14, "no", "x2")]
    assert out[0].ts.tzinfo is not None and out[0].ts > out[1].ts
    (call,) = calls
    assert call[1].endswith("/markets/trades")
    assert call[2]["ticker"] == "KXMLBGAME-26OCT102000CWSCLE-CWS" and call[2]["min_ts"] == int(since.timestamp())


def test_build_exchange_charges_the_kalshi_maker_fee_on_paper_tape_fills(monkeypatch):
    from sportsbot.bot.runner import build_exchange

    monkeypatch.delenv("KALSHI_MAKER_FEE_PER_CONTRACT", raising=False)
    paper, data, fee = build_exchange({"exchange": "kalshi", "mode": "paper",
                                       "bankroll": {"amount": 100.0}})
    assert isinstance(paper, PaperExchange) and paper.data_client is data
    # MLB series carries the 0.5 multiplier: 0.0025 x 0.5 per contract
    assert paper.maker_fee_fn(0.45, 10.0, "KXMLBGAME-26OCT102000CWSCLE-CWS") == pytest.approx(0.0125)
    pm, _, _ = build_exchange({"exchange": "polymarket", "mode": "paper"})
    assert pm.maker_fee_fn(0.45, 10.0, "0xabc") == 0.0
