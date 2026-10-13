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
        calls.append(params["offset"])
        if len(calls) == 1:
            raise _429()
        return [{"timestamp": 1, "price": 0.5, "size": 1, "asset": "t1"}]

    monkeypatch.setattr(PolymarketClient, "_get", fake_get)
    monkeypatch.setattr("sportsbot.exchanges.polymarket.time.sleep", lambda s: slept.append(s))
    rows = PolymarketClient()._get_tape_page("0xabc", 0)
    assert len(rows) == 1 and calls == [0, 0] and slept == [1.0]


def test_client_tape_page_gives_up_after_the_retries_and_other_errors_raise(monkeypatch):
    monkeypatch.setattr(PolymarketClient, "_get", lambda self, url, params=None: (_ for _ in ()).throw(_429()))
    monkeypatch.setattr("sportsbot.exchanges.polymarket.time.sleep", lambda s: None)
    try:
        PolymarketClient()._get_tape_page("0xabc", 0)
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 429
    else:
        raise AssertionError("a persistent 429 must surface, never read as an empty tape")
    req = httpx.Request("GET", "https://x")
    err = httpx.HTTPStatusError("500", request=req, response=httpx.Response(500, request=req))
    monkeypatch.setattr(PolymarketClient, "_get", lambda self, url, params=None: (_ for _ in ()).throw(err))
    try:
        PolymarketClient()._get_tape_page("0xabc", 0)
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
            return [{"timestamp": 5, "proxyWallet": "0xw", "asset": "t1", "side": "BUY", "price": 0.4, "size": 2}]

    class H:
        def get(self, url, params=None):
            return R(next(seq))

    rows = wf.fetch_trades("0xcond", http=H(), max_pages=1)
    assert rows == [{"ts": 5, "wallet": "0xw", "asset": "t1", "side": "BUY", "price": 0.4, "size": 2.0}]
