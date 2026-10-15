"""Fresh-wallet flow: candidate selection, freshness, copy pricing, summary."""
import httpx
import pytest

from sportsbot.backtest import fresh_wallet as fw

NOW = 1_800_000_000.0
DAY = 86400.0


class Http:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None):
        self.calls.append(url)
        if "gamma" in url:
            body = [] if params["offset"] else [
                {"conditionId": "c1", "slug": "will-x-win-the-award", "question": "Will X win the award?",
                 "closed": True, "outcomePrices": '["1", "0"]', "clobTokenIds": '["y1", "n1"]',
                 "endDate": "2027-01-15T07:00:00Z", "feesEnabled": True, "feeSchedule": {"rate": 0.05},
                 "events": [{"slug": "award-2027"}]},
                {"conditionId": "c2", "slug": "nba-lal-gsw", "question": "Lakers vs Warriors", "closed": True,
                 "outcomePrices": '["1", "0"]', "clobTokenIds": '["y2", "n2"]', "endDate": "2027-01-15T07:00:00Z",
                 "events": [{"slug": "nba-lal-gsw"}]}]
        elif "/trades" in url:
            body = [
                {"proxyWallet": "0xnew", "side": "BUY", "price": 0.10, "size": 20000, "timestamp": NOW - 5 * DAY, "outcomeIndex": 0},
                {"proxyWallet": "0xnew", "side": "BUY", "price": 0.12, "size": 20000, "timestamp": NOW - 4 * DAY, "outcomeIndex": 0},
                {"proxyWallet": "0xold", "side": "BUY", "price": 0.30, "size": 5000, "timestamp": NOW - 5 * DAY, "outcomeIndex": 1},
                {"proxyWallet": "0xsmall", "side": "BUY", "price": 0.10, "size": 100, "timestamp": NOW - 5 * DAY, "outcomeIndex": 0},
                {"proxyWallet": "0xfav", "side": "BUY", "price": 0.80, "size": 5000, "timestamp": NOW - 5 * DAY, "outcomeIndex": 0},
                {"proxyWallet": "0xsell", "side": "SELL", "price": 0.10, "size": 50000, "timestamp": NOW - 5 * DAY, "outcomeIndex": 0}]
        elif "/activity" in url:
            first = {"0xnew": NOW - 6 * DAY, "0xold": NOW - 400 * DAY}[params["user"]]
            body = [{"timestamp": first}]
        elif "prices-history" in url:
            t0 = params["startTs"] + 60
            body = {"history": [{"t": t0 + 300, "p": 0.11}, {"t": t0 + 700, "p": 0.20}, {"t": t0 + 3700, "p": 0.40}]}
        else:
            raise AssertionError(url)
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))


def test_fresh_long_shot_buyers_are_the_signal_and_copies_are_priced(tmp_path):
    st = fw.Study(http=Http(), cache=str(tmp_path), sleep=lambda s: None, clock=lambda: NOW)
    res = st.run(days=30)
    assert res["markets"] == 1                                   # the NBA game is excluded
    assert res["candidates"] == 2                                # one row per (wallet, outcome); small/fav/sell dropped
    (sig,) = res["signal"]
    (ctl,) = res["control"]
    assert sig["wallet"] == "0xnew" and sig["price"] == 0.10 and sig["won"] == 1.0
    assert ctl["wallet"] == "0xold" and ctl["won"] == 0.0
    entry = 0.20 + fw.HALF_SPREAD
    assert sig["copy_600"] == pytest.approx(1.0 - entry - 0.05 * entry * (1 - entry))
    entry = 0.40 + fw.HALF_SPREAD
    assert sig["copy_3600"] == pytest.approx(1.0 - entry - 0.05 * entry * (1 - entry))
    # the fade buys the other outcome at 1 - px plus the half-spread; the long shot won, so it loses
    other = (1 - 0.20) + fw.HALF_SPREAD
    assert sig["fade_600"] == pytest.approx(0.0 - other - 0.05 * other * (1 - other))
    s = fw.summarize(res)
    assert s["signal"]["surplus"]["mean"] == pytest.approx(0.90)
    assert s["control"]["surplus"]["mean"] == pytest.approx(-0.30)
    assert "copy +10m" in fw.format_summary(s)
