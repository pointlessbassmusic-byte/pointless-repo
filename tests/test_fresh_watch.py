"""Fresh-wallet fade forward test: signal, delayed fade price, settlement, report."""
import httpx
import pytest

from sportsbot.substrate_bridge import fresh_watch as fw

NOW = 1_800_000_000.0


class Http:
    def __init__(self):
        self.closed = False

    def get(self, url, params=None):
        if "/trades" in url:
            body = [
                {"transactionHash": "0xa", "asset": "y1", "proxyWallet": "0xnew", "side": "BUY", "price": 0.10,
                 "size": 20000, "timestamp": NOW - 700, "conditionId": "c1", "outcomeIndex": 0, "title": "Award?"},
                {"transactionHash": "0xb", "asset": "y1", "proxyWallet": "0xold", "side": "BUY", "price": 0.10,
                 "size": 20000, "timestamp": NOW - 700, "conditionId": "c1", "outcomeIndex": 0, "title": "Award?"},
                {"transactionHash": "0xc", "asset": "y2", "proxyWallet": "0xnew", "side": "BUY", "price": 0.10,
                 "size": 20000, "timestamp": NOW - 700, "conditionId": "c2", "outcomeIndex": 0, "title": "Lakers vs Warriors"},
                {"transactionHash": "0xd", "asset": "y1", "proxyWallet": "0xnew", "side": "BUY", "price": 0.60,
                 "size": 20000, "timestamp": NOW - 700, "conditionId": "c1", "outcomeIndex": 0, "title": "Award?"}]
        elif "gamma" in url:
            cid = dict(params)["condition_ids"]
            closed = dict(params)["closed"] == "true"
            m = {"c1": {"conditionId": "c1", "slug": "will-x-win-the-award", "question": "Will X win the award?",
                        "closed": self.closed, "outcomePrices": '["0", "1"]' if self.closed else '["0.1", "0.9"]',
                        "clobTokenIds": '["y1", "n1"]', "feesEnabled": True, "feeSchedule": {"rate": 0.05},
                        "events": [{"slug": "award"}]},
                 "c2": {"conditionId": "c2", "slug": "nba-lal-gsw", "question": "Lakers vs Warriors", "closed": False,
                        "outcomePrices": '["0.1", "0.9"]', "clobTokenIds": '["y2", "n2"]',
                        "events": [{"slug": "nba-lal-gsw"}]}}[cid]
            body = [m] if m["closed"] == closed else []
        elif "/activity" in url:
            body = [{"timestamp": {"0xnew": NOW - 3 * 86400, "0xold": NOW - 300 * 86400}[params["user"]]}]
        elif "/book" in url:
            assert params["token_id"] == "n1"
            body = {"asks": [{"price": "0.88", "size": "100"}, {"price": "0.87", "size": "50"}], "bids": []}
        else:
            raise AssertionError(url)
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))


def test_signal_fade_and_settlement(tmp_path):
    http = Http()
    w = fw.Watch(str(tmp_path / "f.sqlite"), http=http, clock=lambda: NOW, sleep=lambda s: None)
    assert w.scan() == 1                       # old wallet, sports market and 0.60 price all dropped
    assert w.scan() == 0                       # the same trades are not counted twice
    assert w.price_fades() == 1
    row = w.conn.execute("SELECT fade_ask, other_token FROM signals").fetchone()
    assert row == (0.87, "n1")
    http.closed = True
    assert w.settle() == 1
    r = fw.report(str(tmp_path / "f.sqlite"))
    assert r["graded"] == 1 and r["mean"] == pytest.approx(1 - 0.87 - 0.05 * 0.87 * 0.13)
    assert r["verdict"] == "collecting"
