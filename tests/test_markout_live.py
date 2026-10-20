"""Live fill markouts (bot/markout.py): scheduling from booked fills, marking
against the quote at each horizon, side framing, restart safety, the
missed-quote grace, and the per-book report."""

import pytest

from sportsbot.bot.executor import Executor
from sportsbot.bot.markout import HORIZONS, MarkoutRecorder, markout_report
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
)
from sportsbot.data.store import Store


class _Quotes:
    exchange = Exchange.KALSHI

    def __init__(self, mids):
        self.mids = dict(mids)       # market_id -> mid, None = no two-sided book
        self.calls = 0

    def get_quote(self, market):
        self.calls += 1
        mid = self.mids.get(market.market_id)
        if mid is None:
            return MarketQuote(market_id=market.market_id)
        return MarketQuote(market_id=market.market_id, bid=mid - 0.01, ask=mid + 0.01,
                           bids=[BookLevel(price=mid - 0.01, size=10)],
                           asks=[BookLevel(price=mid + 0.01, size=10)])


def _market(mid="m", sport=Sport.TENNIS):
    return MarketInfo(exchange=Exchange.KALSHI, market_id=mid, sport=sport, home="A", away="B")


def _bet(store, mode="live", side="yes", market="m", price=0.50):
    return store.record_bet(market_id=market, sport="tennis", side=side, model_prob=0.6,
                            entry_price=price, stake=5.0, size=10, edge=0.05,
                            exchange="kalshi", mode=mode)


def test_marks_are_framed_to_the_side_bought_and_signed_against_us(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    venue = _Quotes({"m": 0.48})                     # price fell 2c after both fills
    rec = MarkoutRecorder(store, venue)
    yes_id = _bet(store, side="yes", price=0.50)
    no_id = _bet(store, side="no", price=0.50)
    t0 = 1000.0
    assert rec.schedule(yes_id, _market(), Side.YES, 0.50, now=t0) == len(HORIZONS)
    rec.schedule(no_id, _market(), "no", 0.50, now=t0)

    assert rec.process_due(now=t0 + 1) == 0         # nothing due yet
    assert rec.process_due(now=t0 + 5) == 2         # the +5 s row of each fill
    assert venue.calls == 1                         # one quote per market per pass
    rows = {(r["bet_id"], r["horizon_s"]): dict(r) for r in
            store.conn.execute("SELECT * FROM fill_marks WHERE mark_ts IS NOT NULL")}
    assert rows[(yes_id, 5)]["markout"] == pytest.approx(-0.02)   # YES holder lost 2c
    assert rows[(no_id, 5)]["markout"] == pytest.approx(0.02)     # NO holder gained 2c
    assert rows[(no_id, 5)]["mark_price"] == pytest.approx(0.52)

    rep = markout_report(store, mode="live")
    assert rep["n_fills"] == 2
    cell = rep["by_sport"]["tennis"][5]
    assert cell["n"] == 2 and cell["mean_cents"] == pytest.approx(0.0, abs=1e-6)
    assert markout_report(store, mode="paper")["by_sport"] == {}   # other book: nothing


def test_unquotable_market_waits_then_is_recorded_as_missed(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    venue = _Quotes({"m": None})
    rec = MarkoutRecorder(store, venue, horizons=(5,))
    bet = _bet(store)
    rec.schedule(bet, _market(), Side.YES, 0.5, now=0.0)
    assert rec.process_due(now=10.0) == 0           # within grace: left pending
    assert rec.process_due(now=10.0 + 601) == 1     # past grace: missed
    rep = markout_report(store, mode="live")
    assert rep["by_sport"]["tennis"][5] == {"n": 0, "mean_cents": None, "t": None, "missed": 1}


def test_pending_marks_survive_a_restart_with_the_stored_market(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    rec = MarkoutRecorder(store, _Quotes({}), horizons=(60,))
    rec.schedule(_bet(store), MarketInfo(exchange=Exchange.POLYMARKET, market_id="m",
                                         yes_token_id="tok", sport=Sport.TENNIS),
                 Side.YES, 0.5, now=0.0)

    class _Needs:
        exchange = Exchange.POLYMARKET

        def get_quote(self, market):
            assert market.yes_token_id == "tok"     # rebuilt from the row, not the process
            return MarketQuote(market_id="m", bid=0.54, ask=0.56)

    fresh = MarkoutRecorder(store, _Needs())
    assert fresh.process_due(now=100.0) == 1
    assert store.conn.execute("SELECT markout FROM fill_marks").fetchone()[0] == pytest.approx(0.05)


def test_executor_schedules_marks_for_every_booked_fill(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    rec = MarkoutRecorder(store, _Quotes({"m": 0.5}))

    class _Venue:
        exchange = Exchange.KALSHI

        def place_order(self, order):
            order.order_id, order.status, order.filled = "o", OrderStatus.FILLED, order.size
            return order

    ex = Executor(_Venue(), store, mode="live")
    ex.on_fill = rec.schedule
    ex.submit(BetIntent(intent_id="i", market=_market(), side=Side.YES, prob=0.6, price=0.49,
                        size=3, edge=0.05, kelly_fraction=0.01))
    n = store.conn.execute("SELECT COUNT(*) FROM fill_marks WHERE mark_ts IS NULL").fetchone()[0]
    assert n == len(HORIZONS)


def test_kalshi_maker_orders_are_post_only(monkeypatch):
    from sportsbot.exchanges.kalshi import KalshiClient

    client = KalshiClient.__new__(KalshiClient)
    sent = {}

    def fake_request(method, url, json_body=None, auth=False, **kw):
        sent.update(json_body)
        return {"order": {"order_id": "k1", "fill_count": "0"}}

    monkeypatch.setattr(client, "_request_once", fake_request)
    client.place_order(Order(market_id="T", side=Side.YES, price=0.5, size=2, post_only=True))
    assert sent["post_only"] is True and sent["side"] == "bid"
    sent.clear()
    client.place_order(Order(market_id="T", side=Side.NO, price=0.4, size=2))
    assert "post_only" not in sent and sent["side"] == "ask" and sent["price"] == "0.6000"
