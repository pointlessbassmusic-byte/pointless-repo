"""Fed decision watcher: parsing both venues, the rule's three gates (price,
lead window, cross-venue agreement), first-fire persistence, settlement via the
Kalshi counterpart, and the scorecard arithmetic."""
import sqlite3
from datetime import datetime, timedelta, timezone

from src.feeds import BinaryMarket
from src.fed_watch import (
    FedWatcher, group_buckets, parse_kalshi, parse_polymarket, record, scorecard,
    settle, state_lines,
)

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
DECISION = datetime(2026, 10, 28, 17, 59, tzinfo=timezone.utc)   # Kalshi close_time
POLY_END = datetime(2026, 10, 29, 3, 59, tzinfo=timezone.utc)    # Gamma endDate, next morning


def kalshi(suffix, bid, ask, close=DECISION, meeting="26OCT"):
    return BinaryMarket(platform="kalshi", market_id=f"KXFEDDECISION-{meeting}-{suffix}",
                        question="Fed decision in Oct 2026?", yes_bid=bid, yes_ask=ask,
                        no_bid=None, no_ask=None, volume=1e5, close_time=close)


def poly(question, bid, ask, close=POLY_END, token="tok"):
    return BinaryMarket(platform="polymarket", market_id=token, question=question,
                        yes_bid=bid, yes_ask=ask, no_bid=None, no_ask=None,
                        volume=1e6, close_time=close)


HIKE_Q = "Will the Fed increase interest rates by 25 bps after the October 2026 meeting?"
HOLD_Q = "Will there be no change in Fed interest rates after the October 2026 meeting?"


def test_parsers_agree_on_meeting_and_bucket_across_venues():
    assert parse_kalshi("KXFEDDECISION-26OCT-H25") == ("2026-10", "hike_25")
    assert parse_kalshi("KXFEDDECISION-26OCT-H0") == ("2026-10", "hold")
    assert parse_kalshi("KXFEDDECISION-26OCT-C26") == ("2026-10", "cut_50plus")
    assert parse_kalshi("KXFEDDECISION-27JAN-C25") == ("2027-01", "cut_25")
    assert parse_kalshi("KXFED-26OCT-T4.00") is None            # rate-level series, not a decision
    assert parse_polymarket(HIKE_Q) == ("2026-10", "hike_25")
    assert parse_polymarket(HOLD_Q) == ("2026-10", "hold")
    assert parse_polymarket("Will the Fed decrease interest rates by 50+ bps after the "
                            "October 2026 meeting?") == ("2026-10", "cut_50plus")
    assert parse_polymarket("Will the Fed increase interest rates by 50+ bps after the "
                            "January 2027 meeting?") == ("2027-01", "hike_50plus")
    assert parse_polymarket("How many Fed rate cuts in 2026? 2 cuts") is None


def test_decision_time_is_the_earliest_close_across_venues():
    """Kalshi closes at the decision; Polymarket's endDate runs to the next
    morning. Lead time must count to the decision, not to the later close."""
    st = group_buckets([poly(HIKE_Q, 0.90, 0.92), kalshi("H25", 0.91, 0.92)])[("2026-10", "hike_25")]
    assert st.decision_time == DECISION
    assert st.kalshi.market_id.endswith("H25") and st.polymarket.market_id == "tok"


def test_rule_fires_only_at_price_within_window_with_agreement():
    w = FedWatcher({"min_price": 0.90, "max_lead_days": 7, "agree_min": 0.85})
    k, p = kalshi("H25", 0.91, 0.92), poly(HIKE_Q, 0.91, 0.93)
    fired = w.evaluate([k, p], now=NOW)
    assert sorted(s.platform for s in fired) == ["kalshi", "polymarket"]
    ks = next(s for s in fired if s.platform == "kalshi")
    assert ks.meeting == "2026-10" and ks.bucket == "hike_25"
    assert ks.kalshi_ticker == "KXFEDDECISION-26OCT-H25"
    assert abs(ks.lead_days - 3.25) < 0.01
    assert abs(ks.other_mid - 0.92) < 1e-9
    # Kalshi net return folds the taker fee in; Polymarket pays no fee
    fee = 0.07 * 0.92 * 0.08
    assert abs(ks.net_return - (1 - 0.92 - fee) / 0.92) < 1e-4
    ps = next(s for s in fired if s.platform == "polymarket")
    assert abs(ps.net_return - (1 - 0.93) / 0.93) < 1e-4

    # below the threshold: the 0.80-0.90 band is untested, the rule stays shut
    assert w.evaluate([kalshi("H25", 0.86, 0.88), poly(HIKE_Q, 0.87, 0.89)], now=NOW) == []
    # a month out: the edge does not exist yet
    assert w.evaluate([k, p], now=NOW - timedelta(days=30)) == []
    # after the decision: nothing to buy
    assert w.evaluate([k, p], now=DECISION + timedelta(minutes=1)) == []
    # one-sided book on the firing venue: no entry price to record
    assert w.evaluate([kalshi("H25", None, 0.92), p], now=NOW) == [
        s for s in w.evaluate([kalshi("H25", None, 0.92), p], now=NOW) if s.platform == "polymarket"]


def test_rule_needs_the_other_venue_to_agree():
    w = FedWatcher({"min_price": 0.90, "agree_min": 0.85})
    # the other venue prices the same bucket as a coin flip: venue quirk, not fired
    assert w.evaluate([kalshi("H25", 0.91, 0.92), poly(HIKE_Q, 0.50, 0.52)], now=NOW) == []
    # the other venue has no market for the bucket at all: not fired either
    assert w.evaluate([kalshi("H25", 0.91, 0.92)], now=NOW) == []
    # a different bucket on the other venue does not count as agreement
    assert w.evaluate([kalshi("H25", 0.91, 0.92), poly(HOLD_Q, 0.91, 0.93)], now=NOW) == []


def test_record_keeps_the_first_fire_and_settles_on_the_kalshi_counterpart():
    conn = sqlite3.connect(":memory:")
    w = FedWatcher({"min_price": 0.90, "agree_min": 0.85, "stake_usd": 25})
    first = w.evaluate([kalshi("H25", 0.90, 0.91), poly(HIKE_Q, 0.90, 0.92)], now=NOW)
    assert record(conn, first) == 2
    # the same buckets a cycle later, higher: the rule already bought, entry stays
    later = w.evaluate([kalshi("H25", 0.95, 0.96), poly(HIKE_Q, 0.95, 0.97)],
                       now=NOW + timedelta(hours=1))
    assert record(conn, later) == 0
    asks = dict(conn.execute("SELECT platform, ask FROM fed_signals").fetchall())
    assert asks == {"kalshi": 0.91, "polymarket": 0.92}
    assert conn.execute("SELECT DISTINCT stake_usd FROM fed_signals").fetchone()[0] == 25

    # nothing settles on an unrelated ticker; both rows settle on the Kalshi one
    assert settle(conn, {"KXFEDDECISION-26OCT-H0": "no"}) == 0
    assert settle(conn, {"KXFEDDECISION-26OCT-H25": "yes"}) == 2
    assert settle(conn, {"KXFEDDECISION-26OCT-H25": "yes"}) == 0   # already scored
    assert conn.execute("SELECT COUNT(*) FROM fed_signals WHERE result='yes'").fetchone()[0] == 2


def test_scorecard_counts_losses_as_the_whole_stake_and_bounds_the_surprise_rate():
    conn = sqlite3.connect(":memory:")
    w = FedWatcher({"min_price": 0.90, "agree_min": 0.85})
    for meeting, res in (("26OCT", "yes"), ("26DEC", "yes"), ("27JAN", "no")):
        close = DECISION + timedelta(days=60 * ["26OCT", "26DEC", "27JAN"].index(meeting))
        k = kalshi("H25", 0.95, 0.96, close=close, meeting=meeting)
        rows = w.evaluate([k, poly(HIKE_Q.replace("October 2026", "January 2027") if meeting == "27JAN"
                                   else HIKE_Q.replace("October 2026", "December 2026") if meeting == "26DEC"
                                   else HIKE_Q, 0.95, 0.96, close=close)], now=close - timedelta(days=2))
        assert [s.platform for s in rows] == ["kalshi", "polymarket"]
        record(conn, rows[:1])
        settle(conn, {k.market_id: res})
    rec = scorecard(conn)
    assert (rec.n_fired, rec.n_settled, rec.n_paid) == (3, 3, 2)
    win = (1 - 0.96 - 0.07 * 0.96 * 0.04) / 0.96
    assert abs(rec.mean_net_return - (2 * win - 1) / 3) < 1e-3
    assert abs(rec.breakeven_surprise - win / (1 + win)) < 1e-3
    assert abs(rec.surprise_upper95 - 1 / 3) < 1e-3          # a loss was observed

    # zero losses: rule of three, not zero
    clean = sqlite3.connect(":memory:")
    rows = w.evaluate([kalshi("H25", 0.95, 0.96), poly(HIKE_Q, 0.95, 0.96)], now=NOW)
    record(clean, rows[:1])
    settle(clean, {"KXFEDDECISION-26OCT-H25": "yes"})
    assert scorecard(clean).surprise_upper95 == 3.0
    assert scorecard(sqlite3.connect(":memory:")).surprise_upper95 is None


def test_state_lines_show_both_venues_per_bucket():
    lines = state_lines([kalshi("H0", 0.46, 0.48), kalshi("H25", 0.50, 0.52),
                         poly(HOLD_Q, 0.45, 0.47), poly(HIKE_Q, 0.52, 0.54)], now=NOW)
    assert lines[0].startswith("2026-10") and "3.2d out" in lines[0]
    assert any("hike_25" in ln and "0.51" in ln and "0.53" in ln for ln in lines)
    assert any("hold" in ln and "0.47" in ln and "0.46" in ln for ln in lines)
