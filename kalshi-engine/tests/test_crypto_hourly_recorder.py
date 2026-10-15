"""Forward recorder: book and spot parsing, one recorded hour, settlement."""
import pytest

from src import crypto_hourly_recorder as cr


def test_kalshi_top_turns_the_no_ladder_into_yes_asks():
    ob = {"orderbook_fp": {"yes_dollars": [["0.4100", "120.00"], ["0.4200", "50.00"]],
                           "no_dollars": [["0.5500", "80.00"], ["0.5600", "10.00"]]}}
    assert cr.kalshi_top(ob) == (0.42, 0.44, 50.0, 10.0)
    assert cr.kalshi_top({"orderbook_fp": {}}) == (None, None, 0.0, 0.0)


def test_spot_parsers():
    assert cr.kraken_mid({"error": [], "result": {"XXBTZUSD": {"b": ["100.0"], "a": ["102.0"]}}}) == 101.0
    assert cr.kraken_mid({"error": ["EQuery:Unknown asset pair"]}) is None
    assert cr.coinbase_mid({"bid": "99", "ask": "101"}) == 100.0
    assert cr.coinbase_mid({}) is None


class Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if url.endswith("/events"):
            return Resp({"events": [{"event_ticker": "KXBTCD-X", "markets": [
                {"ticker": "KXBTCD-X-T100", "strike_type": "greater", "floor_strike": 100.0,
                 "close_time": "2027-01-15T09:00:00Z"},
                {"ticker": "KXBTCD-X-T150", "strike_type": "greater", "floor_strike": 150.0,
                 "close_time": "2027-01-15T09:00:00Z"}]}]})
        if "kraken" in url:
            return Resp({"error": [], "result": {"XXBTZUSD": {"b": ["100.4"], "a": ["100.6"]}}})
        if "coinbase" in url:
            return Resp({"bid": "100.3", "ask": "100.7"})
        if url.endswith("/orderbook"):
            return Resp({"orderbook_fp": {"yes_dollars": [["0.60", "10"]], "no_dollars": [["0.38", "5"]]}})
        if "/markets/" in url:
            return Resp({"market": {"result": "yes"}})
        raise AssertionError(url)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.5)


def test_one_recorded_hour_and_settlement(tmp_path):
    close = 1800003600.0                              # 2027-01-15T09:00:00Z
    clock = Clock(close - 25 * 60)
    rec = cr.Recorder("KXBTCD", db=str(tmp_path / "r.sqlite"), session=FakeSession(clock),
                      clock=clock, sleep=clock.sleep)
    rows = rec.record_hour(lead_min=2, interval=30)
    assert rows == 4                                  # only the near-spot strike, sampled from close-2min
    assert rec.conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0] == 1
    q = rec.conn.execute("SELECT bid, ask FROM quotes LIMIT 1").fetchone()
    assert q == (0.60, pytest.approx(0.62))
    venues = {r[0] for r in rec.conn.execute("SELECT venue FROM spot")}
    assert venues == {"kraken", "coinbase"}
    clock.t = close + 600
    assert rec.settle() == 1
    assert rec.conn.execute("SELECT result FROM markets").fetchone()[0] == "yes"
