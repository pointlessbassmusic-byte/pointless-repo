"""Wallet review: grading a wallet's trades and pricing their mirror."""
import httpx
import pytest

from sportsbot.backtest import wallet_review as wr

ADDR = "0x" + "ab" * 20
T0 = 1_800_000_000


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        for frag, fn in self.routes.items():
            if frag in url:
                body = fn(params)
                return httpx.Response(200, json=body, request=httpx.Request("GET", url))
        return httpx.Response(404, json={}, request=httpx.Request("GET", url))


def _activity(params):
    if params["offset"] > 0:
        return []
    return [
        # bought the favourite at 0.80; it lost
        {"type": "TRADE", "side": "BUY", "conditionId": "c1", "outcomeIndex": 0, "price": 0.80, "size": 10,
         "usdcSize": 8.0, "timestamp": T0, "slug": "nba-lal-gsw", "eventSlug": "nba-lal-gsw", "title": "Lakers vs Warriors"},
        # sold the Yes at 0.30 on a market that resolved Yes
        {"type": "TRADE", "side": "SELL", "conditionId": "c2", "outcomeIndex": 0, "price": 0.30, "size": 5,
         "usdcSize": 1.5, "timestamp": T0 + 100, "slug": "fed-decision", "eventSlug": "fed-decision", "title": "Fed cut?"},
        {"type": "REDEEM", "conditionId": "c1", "timestamp": T0 + 9000},
        # unresolved market: no settlement, no mirror
        {"type": "TRADE", "side": "BUY", "conditionId": "c3", "outcomeIndex": 1, "price": 0.50, "size": 2,
         "usdcSize": 1.0, "timestamp": T0 + 200, "slug": "will-x-happen", "title": "X?"},
    ]


def _markets(params):
    closed = dict(params)["closed"]
    ms = {"c1": {"conditionId": "c1", "slug": "nba-lal-gsw", "closed": True, "outcomePrices": '["0", "1"]',
                 "clobTokenIds": '["t1a", "t1b"]', "endDate": "2027-01-17T08:00:00Z",
                 "gameStartTime": "2027-01-17 03:00:00+00", "feesEnabled": True,
                 "feeSchedule": {"rate": 0.03}},
          "c2": {"conditionId": "c2", "slug": "fed-decision", "closed": True, "outcomePrices": '["1", "0"]',
                 "clobTokenIds": '["t2a", "t2b"]', "endDate": "2027-01-20T00:00:00Z", "feesEnabled": True,
                 "feeSchedule": {"rate": 0.05}},
          "c3": {"conditionId": "c3", "slug": "will-x-happen", "closed": False, "outcomePrices": '["0.4", "0.6"]',
                 "clobTokenIds": '["t3a", "t3b"]', "endDate": "2027-03-01T00:00:00Z"}}
    want = [v for k, v in params if k == "condition_ids"]
    return [m for c, m in ms.items() if c in want and str(m["closed"]).lower() == closed]


def _history(params):
    tok = params["market"]
    if tok == "t1a":            # the favourite drifted down after the buy
        return {"history": [{"t": T0 - 60, "p": 0.80}, {"t": T0 + 1800, "p": 0.78}, {"t": T0 + 3600, "p": 0.75},
                            {"t": T0 + 86400, "p": 0.70}]}
    return {"history": []}


def _review():
    http = FakeHttp({"/activity": _activity, "/markets": _markets, "/prices-history": _history})
    return wr.WalletReview(ADDR, http=http, sleep=lambda s: None), http


def test_address_is_validated():
    with pytest.raises(ValueError):
        wr.WalletReview("not-an-address", http=object())


def test_grades_settlement_mirror_and_markouts():
    r, http = _review()
    g = {x.condition_id: x for x in r.run()}
    assert set(g) == {"c1", "c2", "c3"}                      # REDEEM rows are not trades
    buy = g["c1"]
    assert buy.category == "sports" and buy.payout == 0.0
    assert buy.pnl_share == pytest.approx(-0.80)
    # mirror: buy the other side at 0.20 + 1c, pay 0.03 x 0.2 x 0.8, collect 1
    assert buy.mirror_share == pytest.approx(0.80 - 0.01 - 0.03 * 0.2 * 0.8)
    assert buy.markout_1h == pytest.approx(0.75 - 0.80)
    assert buy.markout_24h == pytest.approx(0.70 - 0.80)    # game starts later than 24 h
    sell = g["c2"]
    assert sell.category == "economics" and sell.pnl_share == pytest.approx(0.30 - 1.0)
    assert sell.mirror_share == pytest.approx(0.70 - 0.01 - 0.05 * 0.3 * 0.7)
    open_ = g["c3"]
    assert open_.payout is None and open_.pnl_share is None and open_.mirror_share is None
    # closed and open markets are both asked for
    closed_flags = {dict(p)["closed"] for u, p in http.calls if "/markets" in u}
    assert closed_flags == {"true", "false"}


def test_markout_never_reads_past_the_game_start():
    r, _ = _review()
    rows = _activity({"offset": 0})
    mk = r.markets(["c1"])
    mk["c1"].start_ts = T0 + 2400                              # game starts 40 minutes after the buy
    (g,) = [x for x in r.grade(rows, mk) if x.condition_id == "c1"]
    assert g.markout_1h == pytest.approx(0.78 - 0.80)         # the 30-min print, not the in-play 60-min one
    assert g.lead_h == pytest.approx(2400 / 3600)


def test_summary_buckets_and_mirror_totals():
    r, _ = _review()
    res = wr.summarize(r.run())
    a = res["all"]
    assert a["trades"] == 3 and a["settled"] == 2
    assert a["pnl_usd"] == pytest.approx(-0.80 * 10 + (0.30 - 1.0) * 5)
    assert a["mirror_pnl_usd"] > 0 > a["pnl_usd"]
    assert set(res["by"]["category"]) == {"sports", "economics", "other"}
    assert res["by"]["price"]["0.70-0.90"]["trades"] == 1
    text = wr.format_summary(res)
    assert "mirror" in text and "by category" in text


def test_categories_and_buckets():
    assert wr.category("btc-up-or-down-15m") == "crypto"
    assert wr.category("will-trump-win") == "politics"
    assert wr.category("highest-temperature-in-nyc") == "weather"
    assert wr.price_bucket(0.95) == ">=0.90" and wr.lead_bucket(30) == "1-7d" and wr.lead_bucket(None) == "unknown"
