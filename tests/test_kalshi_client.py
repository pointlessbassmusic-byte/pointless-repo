"""Kalshi client audit regressions: orders reconciliation and retry
semantics. Each test pins a defect found by auditing the client against
the live 2026 API surface."""

import httpx
import pytest

from sportsbot.core.types import OrderStatus, Side
from sportsbot.exchanges.kalshi import KalshiClient, KalshiClientError

# ---------------------------------------------------------------------------
# Audit: orders / retry semantics
# ---------------------------------------------------------------------------
class _FakeOrdersClient(KalshiClient):
    """KalshiClient with the network replaced by a canned payload."""

    def __init__(self, payload):
        super().__init__(api_key_id="k", env="prod")
        self._payload = payload

    def _request(self, *a, **kw):
        return self._payload


def test_open_orders_report_fills_so_the_executor_can_book_them():
    """A resting order that partially filled must come back with
    `filled` set. The executor books a maker fill only when the venue
    reports more filled than it already knows, so a hardcoded 0.0 leaves
    live exposure untracked."""
    c = _FakeOrdersClient({"orders": [{
        "order_id": "o1", "client_order_id": "c1", "ticker": "KXMLBGAME-X",
        "side": "bid", "initial_count_fp": "10.00",
        "remaining_count_fp": "4.00", "yes_price_dollars": "0.4300",
    }]})
    o = c.get_open_orders()[0]
    assert o.size == 10.0
    assert o.filled == 6.0
    assert o.status is OrderStatus.PARTIAL
    assert o.side is Side.YES
    assert o.price == 0.43


def test_open_orders_do_not_invert_the_side_on_a_yes_no_payload():
    """`side` is "bid"/"ask" under Create Order V2, but a payload that
    names the outcome directly must not fall through the bid/ask branch
    and come back as the opposite contract."""
    c = _FakeOrdersClient({"orders": [
        {"order_id": "a", "ticker": "T", "side": "yes", "fill_count_fp": "1.00"},
        {"order_id": "b", "ticker": "T", "side": "no", "fill_count_fp": "1.00"},
        {"order_id": "c", "ticker": "T", "side": "ask", "no_price_dollars": "0.3000"},
    ]})
    a, b, cc = c.get_open_orders()
    assert a.side is Side.YES
    assert b.side is Side.NO
    assert cc.side is Side.NO and cc.price == 0.30


def test_open_orders_warn_rather_than_report_a_silent_zero_fill(caplog):
    c = _FakeOrdersClient({"orders": [{"order_id": "o", "ticker": "T", "side": "bid"}]})
    with caplog.at_level("WARNING"):
        o = c.get_open_orders()[0]
    assert o.filled == 0.0
    assert "cannot be reconciled" in caplog.text


def test_client_errors_are_not_retried():
    """A 404 for a settled ticker is an answer, not a transient failure.
    Retrying it five times with backoff stalls the settlement pass."""
    calls = []

    class C(KalshiClient):
        def _request_once(self, method, path, **kw):
            calls.append(path)
            req = httpx.Request(method, "https://x/y")
            resp = httpx.Response(404, request=req, text="not found")
            raise KalshiClientError("404", request=req, response=resp)

    c = C(env="prod")
    with pytest.raises(KalshiClientError):
        c._request("GET", "/trade-api/v2/markets/GONE")
    assert len(calls) == 1
    assert c.get_resolution("GONE") is None


def test_rate_limits_are_still_retried():
    calls = []

    class C(KalshiClient):
        def _request_once(self, method, path, **kw):
            calls.append(path)
            if len(calls) < 3:
                req = httpx.Request(method, "https://x/y")
                raise httpx.HTTPStatusError(
                    "rate limited", request=req, response=httpx.Response(429, request=req))
            return {"ok": True}

    assert C(env="prod")._request("GET", "/p") == {"ok": True}
    assert len(calls) == 3


def test_venue_positions_carry_their_cost():
    class C(KalshiClient):
        def _request(self, *a, **kw):
            return {"market_positions": [
                {"ticker": "T", "position_fp": "-20.00",
                 "market_exposure_dollars": "7.0000"}]}

    p = C(env="prod").get_positions()[0]
    assert p.side is Side.NO and p.size == 20.0
    assert p.avg_price == 0.35
    assert p.cost == 7.0
