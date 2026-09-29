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
