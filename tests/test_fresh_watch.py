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


def test_status_line_for_the_digest_and_board(tmp_path):
    db = str(tmp_path / "f.sqlite")
    assert fw.status_line(db) is None                      # no record on this host
    http = Http()
    w = fw.Watch(db, http=http, clock=lambda: NOW, sleep=lambda s: None)
    w.scan()
    w.price_fades()
    line = fw.status_line(db)
    assert "1 signals in 1 markets, 0 graded" in line
    http.closed = True
    w.settle()
    assert "collecting" in fw.status_line(db) and "pt per share" in fw.status_line(db)


def test_the_board_shows_the_forward_test_card():
    from sportsbot import dashboard
    page = dashboard._forward(["fresh-wallet fade (forward test): 3 signals"])
    assert "fresh-wallet fade" in page
    assert "No forward-test record" in dashboard._forward([])


def test_digest_appends_extra_lines():
    from sportsbot.bot import alerts

    class Store:
        def settled_bets(self, limit, mode):
            return []

    import sportsbot.bot.ledger as ledger
    orig = ledger.account_equity
    ledger.account_equity = lambda store, acct, start: {"equity": 100.0, "realized_pnl": 0.0,
                                                         "open_positions": 0, "settled_bets": 0}
    try:
        text = alerts.digest_text(Store(), "sim", "paper", 100.0, extra=["fresh-wallet fade: x"])
    finally:
        ledger.account_equity = orig
    assert text.splitlines()[-1] == "fresh-wallet fade: x"
