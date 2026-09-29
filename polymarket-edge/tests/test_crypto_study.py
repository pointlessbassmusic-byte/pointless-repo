"""Crypto digitals study: question templates, resolution timing, hourly
series lookups, and the lognormal digital arithmetic."""
import math
from datetime import datetime, timezone

from src.crypto_study import (
    HourlySeries, digital_prob, parse_event, parse_question, prob_above, resolution_time,
)

INF = math.inf
DESC = ('This market will resolve according to the final "Close" price of the Binance 1 minute '
        "candle for BTCUSDT 15 May '25 17:00 in the ET timezone. Otherwise ...")


def test_parse_question_covers_between_greater_less_above_and_k_suffix():
    assert parse_question("Will the price of Bitcoin be between $101K and $102K on May 15 at 5 PM ET?") == ("bitcoin", 101000.0, 102000.0)
    assert parse_question("Will the price of Bitcoin be between $90000 and $88000 on April 4?") == ("bitcoin", 88000.0, 90000.0)
    assert parse_question("Will the price of Ethereum be greater than $2000 on May 2?") == ("ethereum", 2000.0, INF)
    assert parse_question("Will the price of Ethereum be less than $1500 on May 2?") == ("ethereum", -INF, 1500.0)
    assert parse_question("Bitcoin above $70,000 on April 5?") == ("bitcoin", 70000.0, INF)
    # touch options are not digitals
    assert parse_question("Will Bitcoin reach $200,000 by March 31?") is None
    assert parse_question("Will Ethereum dip to $3,500.00 by March 31?") is None
    assert parse_question("Will Bitcoin hit $70k or $90k first?") is None
    assert parse_question("Solana above $175 on April 12?") is None       # no DVOL for SOL


def test_resolution_time_is_the_stated_eastern_time_with_dst():
    assert resolution_time(DESC) == datetime(2025, 5, 15, 21, 0, tzinfo=timezone.utc)
    winter = DESC.replace("15 May '25 17:00", "15 Jan '26 12:00")
    assert resolution_time(winter) == datetime(2026, 1, 15, 17, 0, tzinfo=timezone.utc)
    assert resolution_time("no timing here") is None


def test_parse_event_builds_digitals_with_outcome_from_resolution():
    ev = {"slug": "bitcoin-price-may-15-5pm-et", "markets": [
        {"question": "Will the price of Bitcoin be less than $101K on May 15 at 5 PM ET?",
         "description": DESC, "outcomePrices": '["0", "1"]', "clobTokenIds": '["y1", "n1"]',
         "conditionId": "c1", "volumeNum": "3373"},
        {"question": "Will the price of Bitcoin be between $103K and $104K on May 15 at 5 PM ET?",
         "description": DESC, "outcomePrices": '["1", "0"]', "clobTokenIds": '["y2", "n2"]',
         "conditionId": "c2", "volumeNum": "900"},
        {"question": "Will Bitcoin reach $200,000 by May 15?", "description": DESC,
         "outcomePrices": '["0", "1"]', "clobTokenIds": '["y3", "n3"]', "conditionId": "c3"},
    ]}
    ds = parse_event(ev)
    assert [(d.lo, d.hi, d.outcome, d.yes_token) for d in ds] == [(-INF, 101000.0, 0, "y1"), (103000.0, 104000.0, 1, "y2")]
    assert all(d.asset == "BTC" and d.resolves == datetime(2025, 5, 15, 21, 0, tzinfo=timezone.utc) for d in ds)


def test_resolution_time_reads_the_post_may_2025_wording_from_title_and_end_date():
    desc = ('This market will resolve according to the final "Close" price of the Binance 1 minute '
            "candle for BTC/USDT 12:00 in the ET timezone (noon) on the date specified in the title.")
    q = "Will the price of Bitcoin be less than $72,000 on September 10?"
    assert resolution_time(desc, q, "2026-09-10T16:00:00Z") == datetime(2026, 9, 10, 16, 0, tzinfo=timezone.utc)
    # year rolls with the event: a January title on a December-listed event
    assert resolution_time(desc, "Will the price of Bitcoin be above $90,000 on January 2?",
                           "2026-01-02T17:00:00Z") == datetime(2026, 1, 2, 17, 0, tzinfo=timezone.utc)
    # a title date that does not land near the event end is refused, as is a missing end date
    assert resolution_time(desc, q, "2026-09-20T16:00:00Z") is None
    assert resolution_time(desc, q, "") is None
    assert resolution_time("no time here", q, "2026-09-10T16:00:00Z") is None


def test_hourly_series_lookup_and_realised_vol():
    pts = [(3600 * i, 100.0 * (1.01 ** (i % 2))) for i in range(800)]   # alternating +-1% hourly
    s = HourlySeries(pts)
    assert s.at(3600 * 10 + 1800) == pts[10][1]          # last hour at or before
    assert s.at(3600 * 10 - 1) == pts[9][1]
    assert s.at(3600 * 900) is None                       # more than 3h past the last point
    assert s.at(-1) is None
    rv = s.realised_vol(3600 * 799, window_h=720)
    hourly_sd = math.log(1.01)                            # returns are exactly +-log(1.01)
    assert abs(rv - hourly_sd * math.sqrt(24 * 365.25)) < 0.01
    assert HourlySeries(pts[:100]).realised_vol(3600 * 99, window_h=720) is None   # too short


def test_lognormal_digital_probabilities():
    assert abs(prob_above(100.0, 100.0, 0.5, 1 / (24 * 365.25)) - 0.5) < 0.01   # at the money, an hour out
    assert prob_above(100.0, 120.0, 0.5, 1 / 365.25) < 0.01                      # 20% away in a day at 50% vol
    assert prob_above(100.0, 80.0, 0.5, 1 / 365.25) > 0.99
    assert prob_above(100.0, 0.0, 0.5, 1.0) == 1.0
    p_between = digital_prob(100.0, 99.0, 101.0, 0.5, 1 / 365.25)
    assert 0 < p_between < 1
    lo = digital_prob(100.0, -INF, 99.0, 0.5, 1 / 365.25)
    hi = digital_prob(100.0, 101.0, INF, 0.5, 1 / 365.25)
    assert abs(lo + p_between + hi - 1.0) < 1e-9           # the buckets partition the line


def test_fetch_digitals_skips_5xx_windows_and_caches_complete_ones(tmp_path):
    import json
    from datetime import datetime, timezone
    from src import crypto_study as cs

    class Resp:
        def __init__(self, status, body=None):
            self.status_code = status
            self._body = body if body is not None else []

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    ev = {"slug": "btc-week", "markets": [{
        "question": "Will the price of Bitcoin be between $60K and $62K on March 8 at 12 PM ET?",
        "description": DESC,
        "outcomePrices": json.dumps(["1", "0"]), "clobTokenIds": json.dumps(["y", "n"]),
        "conditionId": "c1", "volumeNum": "1000"}]}

    class Http:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None, timeout=None):
            self.calls.append(params["end_date_min"])
            if params["end_date_min"] == "2024-03-08":
                return Resp(500)
            if params["tag_slug"] == "bitcoin" and params["end_date_min"] == "2024-03-01":
                return Resp(200, [ev])
            return Resp(200, [])

    class FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2024, 3, 20, tzinfo=timezone.utc)

    monkey_now = cs.datetime
    cs.datetime = FixedNow
    try:
        http = Http()
        out = cs.fetch_digitals(http, since="2024-03-01", cache=tmp_path)
        assert [d.condition_id for d in out] == ["c1"]
        cached = json.loads((tmp_path / "crypto_digitals.json").read_text())
        # the 500'd window is skipped, not cached; the good one is cached
        assert "2024-03-01" in cached and "2024-03-08" not in cached
        # a rerun serves the cached window without touching the network for it
        http2 = Http()
        out2 = cs.fetch_digitals(http2, since="2024-03-01", cache=tmp_path)
        assert [d.condition_id for d in out2] == ["c1"]
        assert "2024-03-01" not in http2.calls
    finally:
        cs.datetime = monkey_now
