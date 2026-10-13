"""Central-bank decision study: event classification, the no-lookahead anchor,
first-fire selection, costs, meeting dedupe and the bucket filter."""
import json
from datetime import datetime, timezone

from src.cb_study import (
    anchor_ts, bank_of, buckets, dedupe, entry_return, fee_rate, first_fire,
    rule_of_three, score, taker_fee,
)


def test_bank_of_keeps_only_per_meeting_decision_events():
    assert bank_of("fed-decision-in-september-825") == "fed"
    assert bank_of("ecb-interest-rates-january-2025") == "ecb"
    assert bank_of("bank-of-japan-decision-in-december-425") == "boj"
    assert bank_of("reserve-bank-of-australia-decision-in-february") == "rba"
    assert bank_of("central-bank-of-colombia-decision-in-april") == "colombia"
    # annual, parlay, dissent and "by <date>" events are not decisions
    assert bank_of("ecb-rate-cut-in-2026") is None
    assert bank_of("fed-decision-in-2026") is None
    assert bank_of("dissent-at-fed-meeting") is None
    assert bank_of("fed-3-parlay-december") is None
    assert bank_of("christine-lagarde-out-as-ecb-president-in-2026") is None


def test_anchor_is_utc_midnight_of_the_announcement_date():
    # ECB lists endDate at 12:00Z on the meeting day; the anchor must not
    # move later than that day's start, whatever the time component says
    ts = anchor_ts("2025-01-30T12:00:00Z")
    assert ts == int(datetime(2025, 1, 30, tzinfo=timezone.utc).timestamp())
    assert anchor_ts("2026-01-23T00:00:00Z") == int(
        datetime(2026, 1, 23, tzinfo=timezone.utc).timestamp())


def test_first_fire_takes_the_earliest_horizon_in_band():
    snaps = [(168, 0.85, 0), (120, 0.91, 0), (72, 0.97, 1), (24, None, None)]
    assert first_fire(snaps, 0.90) == (120, 0.91, 0)
    assert first_fire(snaps, 0.80, 0.90) == (168, 0.85, 0)
    assert first_fire([(168, None, None), (24, 0.5, 0)], 0.90) is None


def test_costs_include_spread_and_the_markets_own_fee():
    assert abs(taker_fee(0.95, 0.05) - 0.002375) < 1e-12
    win = entry_return(0.95, True, 0.0)
    assert abs(win - (1 / 0.96 - 1)) < 1e-12
    assert entry_return(0.95, False, 0.05) == -1.0
    assert entry_return(0.95, True, 0.05) < win
    assert fee_rate({"feesEnabled": False, "feeSchedule": {"rate": 0.05}}) == 0.0
    assert fee_rate({"feesEnabled": True, "feeSchedule": {"rate": 0.03}}) == 0.03
    assert fee_rate({"feesEnabled": True}) == 0.05      # silent Gamma: assume, never 0


def _mkt(q, yes, fees=False):
    return {"question": q, "conditionId": q, "outcomes": json.dumps(["Yes", "No"]),
            "outcomePrices": json.dumps(["1", "0"] if yes else ["0", "1"]),
            "clobTokenIds": json.dumps([q + "-y", q + "-n"]), "feesEnabled": fees}


def test_buckets_need_exactly_one_winner():
    ev = {"markets": [_mkt("hold", True), _mkt("cut", False), _mkt("hike", False)]}
    b = buckets(ev)
    assert [x["won"] for x in b] == [True, False, False]
    assert buckets({"markets": [_mkt("hold", True), _mkt("cut", True)]}) is None
    assert buckets({"markets": [_mkt("hold", True)]}) is None


def test_dedupe_merges_one_meeting_listed_twice():
    evs = [{"bank": "israel", "date": "2026-08-31", "volume": 139_000, "slug": "aug"},
           {"bank": "israel", "date": "2026-09-01", "volume": 94_000, "slug": "sep"},
           {"bank": "israel", "date": "2026-10-13", "volume": 50_000, "slug": "oct"},
           {"bank": "fed", "date": "2026-09-01", "volume": 1, "slug": "fed"}]
    assert [e["slug"] for e in dedupe(evs)] == ["fed", "aug", "oct"]


def test_score_reads_the_bucket_that_fired_not_the_winner():
    ev = {"bank": "colombia", "slug": "x", "date": "2026-04-30",
          "buckets": [{"question": "increase", "won": False, "rate": 0.0},
                      {"question": "no change", "won": True, "rate": 0.0}],
          "snaps": [{"h": 168, "p": 0.876, "i": 0, "age_h": 3.6},
                    {"h": 120, "p": 0.91, "i": 0, "age_h": 8.5},
                    {"h": 72, "p": 0.95, "i": 1, "age_h": 3.3}]}
    r = score(ev, 0.90)
    assert (r["h"], r["won"], r["ret"]) == (120, False, -1.0)
    # a stale-print filter can skip the first fire, never invent one
    assert score(ev, 0.90, max_age_h=5)["h"] == 72
    assert rule_of_three(57) == 3 / 57 and rule_of_three(0) is None
