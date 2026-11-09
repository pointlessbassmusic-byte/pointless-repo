"""Tape reads retry on 429: a rate-limited page must not become a missing fill."""

import httpx

from sportsbot.backtest import wallet_follow as wf
from sportsbot.exchanges.polymarket import PolymarketClient


def _429():
    req = httpx.Request("GET", "https://data-api.polymarket.com/trades")
    return httpx.HTTPStatusError("429", request=req, response=httpx.Response(429, request=req))


def test_client_tape_page_retries_a_429_then_succeeds(monkeypatch):
    calls = []
    slept = []

    def fake_get(self, url, params=None):
        calls.append(params.get("cursor"))
        if len(calls) == 1:
            raise _429()
        return {"data": [{"timestamp": 1, "price": 0.5, "size": 1, "token_id": "t1"}],
                "pagination": {"next_cursor": None}}

    monkeypatch.setattr(PolymarketClient, "_get", fake_get)
    monkeypatch.setattr("sportsbot.exchanges.polymarket.time.sleep", lambda s: slept.append(s))
    rows, cursor = PolymarketClient()._get_tape_page("0xabc")
    assert len(rows) == 1 and cursor is None and calls == [None, None] and slept == [1.0]


def test_client_tape_page_gives_up_after_the_retries_and_other_errors_raise(monkeypatch):
    monkeypatch.setattr(PolymarketClient, "_get", lambda self, url, params=None: (_ for _ in ()).throw(_429()))
    monkeypatch.setattr("sportsbot.exchanges.polymarket.time.sleep", lambda s: None)
    try:
        PolymarketClient()._get_tape_page("0xabc")
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 429
    else:
        raise AssertionError("a persistent 429 must surface, never read as an empty tape")
    req = httpx.Request("GET", "https://x")
    err = httpx.HTTPStatusError("500", request=req, response=httpx.Response(500, request=req))
    monkeypatch.setattr(PolymarketClient, "_get", lambda self, url, params=None: (_ for _ in ()).throw(err))
    try:
        PolymarketClient()._get_tape_page("0xabc")
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 500


def test_backtest_tape_fetch_retries_a_429_page(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "TRADES_CACHE", str(tmp_path))
    monkeypatch.setattr(wf.time, "sleep", lambda s: None)
    seq = iter([429, 200])

    class R:
        def __init__(self, code):
            self.status_code = code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("x", request=None, response=None)

        def json(self):
            return {"data": [{"timestamp": 5, "proxy_wallet": "0xw", "token_id": "t1", "side": "BUY",
                              "price": 0.4, "size": 2}], "pagination": {"next_cursor": None}}

    class H:
        def get(self, url, params=None):
            return R(next(seq))

    rows = wf.fetch_trades("0xcond", http=H(), max_pages=1)
    assert rows == [{"ts": 5, "wallet": "0xw", "asset": "t1", "side": "BUY", "price": 0.4, "size": 2.0}]


def test_backtest_tape_fetch_uses_data_api_v2_cursor_pages(tmp_path, monkeypatch):
    """v1 is 410 Gone since 2026-11-02: v2 takes `condition=` and a cursor,
    never `market=` or `offset=`, and wraps rows in {"data", "pagination"}."""
    monkeypatch.setattr(wf, "TRADES_CACHE", str(tmp_path))
    monkeypatch.setattr(wf.time, "sleep", lambda s: None)
    pages = [({"data": [{"timestamp": 9, "proxy_wallet": "0xa", "token_id": "t1", "side": "SELL",
                         "price": 0.6, "size": 1}], "pagination": {"next_cursor": "c2"}}),
             ({"data": [{"timestamp": 3, "proxy_wallet": "0xb", "token_id": "t2", "side": "BUY",
                         "price": 0.3, "size": 4}], "pagination": {"next_cursor": None}})]
    calls = []

    class R:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    class H:
        def get(self, url, params=None):
            calls.append((url, dict(params)))
            return R(pages[len(calls) - 1])

    rows = wf.fetch_trades("0xcond", http=H())
    assert [c[0].endswith("/v2/trades") for c in calls] == [True, True]
    assert calls[0][1]["condition"] == "0xcond" and "market" not in calls[0][1] and "offset" not in calls[0][1]
    assert "cursor" not in calls[0][1] and calls[1][1]["cursor"] == "c2"
    assert [r["ts"] for r in rows] == [3, 9] and rows[0]["asset"] == "t2" and rows[1]["wallet"] == "0xa"
