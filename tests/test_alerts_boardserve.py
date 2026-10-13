"""Operator alerts (webhook, dedupe, digest) and the whitelisted board server."""

import threading
import urllib.request
from datetime import datetime, timezone

import pytest

from sportsbot.bot import alerts
from sportsbot.boardserve import ALLOWED, make_handler, resolve, with_switcher
from sportsbot.data.store import Store


class _Http:
    def __init__(self, status=204):
        self.calls = []
        self.status = status

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return type("R", (), {"status_code": self.status})()


def test_notify_is_a_no_op_without_a_webhook_and_dedupes_with_one(monkeypatch):
    alerts.reset()
    monkeypatch.delenv(alerts.ENV_WEBHOOK, raising=False)
    h = _Http()
    assert alerts.notify("fill", "x", http=h) is False and h.calls == []
    monkeypatch.setenv(alerts.ENV_WEBHOOK, "https://discord.example/hook")
    assert alerts.notify("halt", "blocked: kill switch", http=h, now=1000.0) is True
    assert alerts.notify("halt", "blocked: kill switch", http=h, now=1500.0) is False   # inside dedupe
    assert alerts.notify("halt", "blocked: kill switch", http=h, now=1000.0 + 3601) is True
    assert alerts.notify("fill", "1 fill", http=h, dedupe_seconds=0.0, now=1000.0) is True
    assert alerts.notify("fill", "1 fill", http=h, dedupe_seconds=0.0, now=1000.0) is True
    assert len(h.calls) == 4 and h.calls[0][1]["content"].startswith("**sportsbot halt**")
    # a failing webhook never raises and reports False
    bad = _Http(status=500)
    assert alerts.notify("fill", "y", http=bad, dedupe_seconds=0.0) is False

    class Boom:
        def post(self, *a, **k):
            raise RuntimeError("down")
    assert alerts.notify("fill", "z", http=Boom(), dedupe_seconds=0.0) is False


def test_digest_text_and_touch_today(tmp_path):
    store = Store(str(tmp_path / "t.sqlite"))
    today = datetime.now(timezone.utc).date().isoformat()
    store.record_order(client_id="a", order_id="a", market_id="m1", side="yes", price=0.44, size=2.0,
                       filled=2.0, status="filled",
                       raw={"arm": "baseball/touch/maker", "placed_at": f"{today}T12:00:00+00:00"})
    store.record_order(client_id="b", order_id="b", market_id="m1", side="no", price=0.53, size=2.0,
                       filled=0.0, status="canceled",
                       raw={"arm": "baseball/touch/maker", "placed_at": f"{today}T12:00:00+00:00"})
    store.record_order(client_id="c", order_id="c", market_id="m2", side="yes", price=0.5, size=2.0,
                       filled=0.0, status="canceled",
                       raw={"arm": "baseball/model/maker", "placed_at": f"{today}T12:00:00+00:00"})
    store.record_order(client_id="d", order_id="d", market_id="m3", side="yes", price=0.5, size=2.0,
                       filled=1.0, status="filled",
                       raw={"arm": "baseball/touch/maker", "placed_at": "2000-01-01T00:00:00+00:00"})
    assert alerts.touch_today(store) == {"posted": 2, "filled": 1, "canceled": 1}
    bid = store.record_bet(market_id="m1", sport="baseball", side="yes", model_prob=0.5, entry_price=0.44,
                           stake=0.88, size=2.0, edge=0.01, exchange="paper", mode="paper",
                           arm="baseball/touch/maker")
    store.settle_bet(bid, outcome=1, pnl=1.12, closing_price=0.50)
    text = alerts.digest_text(store, "sim", "paper", 100.0, touch=alerts.touch_today(store),
                              edge_rows=[{"sport": "baseball", "best": 0.0259, "bar": 0.03, "priced": 8}])
    assert "equity $101.12" in text and "1 settled, mean CLV +6.00 pt on 1" in text
    assert "touch today: posted 2, filled 1, cancelled 1" in text
    assert "baseball best +2.59 vs bar 3.0 pt (8 priced)" in text
    assert "real/live: equity $0.00" in alerts.digest_text(store, "real", "live", 0.0)


def test_board_server_serves_only_the_whitelist(tmp_path):
    (tmp_path / "board.html").write_text("<title>sportsbot board</title>")
    (tmp_path / "sportsbot.sqlite").write_bytes(b"secret")
    assert resolve("/") == "board.html" and resolve("/sportsbot/") == "board.html"
    assert resolve("/sportsbot/board.html?x=1") == "board.html"
    assert resolve("/dashboard.html") == "dashboard.html"
    assert resolve("/sportsbot.sqlite") is None and resolve("/../etc/passwd") is None
    assert set(ALLOWED) == {"board.html", "dashboard.html"}
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(tmp_path)))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/sportsbot/") as r:
            assert r.status == 200 and b"sportsbot board" in r.read()
            assert r.headers["Cache-Control"] == "no-store"
        for bad in ("/sportsbot.sqlite", "/data/sportsbot.sqlite", "/x.html"):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{bad}")
            assert e.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/dashboard.html")   # whitelisted, not rendered
        assert e.value.code == 503
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_desk_switcher_is_added_only_under_the_tailscale_prefix(tmp_path):
    """At /sportsbot the board sits beside the crypto desk at /: the server
    adds a script-free strip linking the two; served any other way, or
    written to disk, the page is untouched."""
    page = b"<!doctype html><html><head><title>t</title></head><body><div class=\"wrap\">x</div></body></html>"
    out = with_switcher(page, "board.html", "/sportsbot/")
    assert out.startswith(b"<!doctype html><html><head><title>t</title></head><body><style>.hub{")
    assert b'<nav class="hub"><a href="/">Crypto desk</a><span class="current">Sports book</span>' in out
    assert b'<a href="/sportsbot/dashboard.html">Substrate</a></nav><div class="wrap">' in out
    assert b"<script" not in out
    sub = with_switcher(page, "dashboard.html", "/sportsbot/dashboard.html")
    assert b'<a href="/sportsbot/">Sports book</a><span class="current">Substrate</span>' in sub
    assert with_switcher(page, "board.html", "/") == page           # plain localhost: no desk at /
    assert with_switcher(page, "board.html", "/board.html") == page
    # a page without a body tag still gets the strip, in front
    assert with_switcher(b"<h1>bare</h1>", "board.html", "/sportsbot/").endswith(b"</nav><h1>bare</h1>")
    # end to end through the socket
    (tmp_path / "board.html").write_bytes(page)
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(tmp_path)))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/sportsbot/") as r:
            served = r.read()
        assert b'<nav class="hub">' in served and b"<script" not in served
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            assert r.read() == page
    finally:
        httpd.shutdown()
        httpd.server_close()
