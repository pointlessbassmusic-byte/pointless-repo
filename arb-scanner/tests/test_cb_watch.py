"""G10 central-bank recorder: both venues' wording for all six banks, the
Fed gates where Kalshi lists the bank, the single-venue path and its lead
floor where it does not, settlement by ticker or by token, and that the Fed
watcher's own behaviour is unchanged."""
import sqlite3
from datetime import datetime, timedelta, timezone

from src.cb_watch import (
    EXPORT_FIELDS, evaluate, export_csv, open_poly_tokens, parse_kalshi, parse_polymarket,
    record, scorecard, settle,
)
from src.fed_watch import FedWatcher
from src.feeds import BinaryMarket

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
BOE_K_CLOSE = datetime(2026, 11, 5, 11, 54, tzinfo=timezone.utc)
BOE_P_END = datetime(2026, 11, 5, 23, 59, tzinfo=timezone.utc)
RBA_P_END = datetime(2026, 11, 4, 4, 59, tzinfo=timezone.utc)   # a day after the decision

BOE_HIKE = "Bank of England increases interest rates by 25 bps after November 2026 meeting?"
RBA_HOLD = "No change in Reserve Bank of Australia's interest rates at the November 2026 meeting?"


def k(ticker, bid, ask, close=BOE_K_CLOSE):
    return BinaryMarket(platform="kalshi", market_id=ticker, question="", yes_bid=bid,
                        yes_ask=ask, no_bid=None, no_ask=None, volume=1e4, close_time=close)


def p(question, bid, ask, close, token="tok"):
    return BinaryMarket(platform="polymarket", market_id=token, question=question,
                        yes_bid=bid, yes_ask=ask, no_bid=None, no_ask=None, volume=1e5,
                        close_time=close)


def test_kalshi_tickers_for_every_listed_bank():
    assert parse_kalshi("KXCBDECISIONENGLAND-26NOV05-H25") == ("boe 2026-11", "hike_25")
    assert parse_kalshi("KXCBDECISIONENGLAND-26NOV05-H25P") == ("boe 2026-11", "hike_50plus")
    assert parse_kalshi("KXCBDECISIONJAPAN-26OCT29-HOLD") == ("boj 2026-10", "hold")
    assert parse_kalshi("KXCBDECISIONCANADA-26OCT-H0") == ("boc 2026-10", "hold")
    assert parse_kalshi("KXCBDECISIONCANADA-26DEC-C26") == ("boc 2026-12", "cut_50plus")
    assert parse_kalshi("KXCBDECISIONNZ-26OCT27-C25P") == ("rbnz 2026-10", "cut_50plus")
    assert parse_kalshi("KXCBDECISIONBRAZIL-26OCT-H0") is None      # not G10
    assert parse_kalshi("KXFEDDECISION-26OCT-H0") is None           # the Fed has its own module


def test_polymarket_wording_for_all_six_banks():
    close = datetime(2026, 10, 28, tzinfo=timezone.utc)
    cases = {
        "Will the ECB announce a 25 bps decrease at the October 2026 meeting?": ("ecb 2026-10", "cut_25"),
        "Will the ECB announce no change at the October 2026 meeting?": ("ecb 2026-10", "hold"),
        "Will the ECB announce a 50+ bps increase at the October 2026 meeting?": ("ecb 2026-10", "hike_50plus"),
        BOE_HIKE: ("boe 2026-11", "hike_25"),
        "No change in Bank of Japan’s interest rates after the October 2026 meeting?": ("boj 2026-10", "hold"),
        "Bank of Japan decreases interest rates by 50+ bps after the December 2026 meeting?": ("boj 2026-12", "cut_50plus"),
        RBA_HOLD: ("rba 2026-11", "hold"),
        "Reserve Bank of New Zealand increases interest rates by 25 bps at the October 2026 meeting?": ("rbnz 2026-10", "hike_25"),
    }
    for q, want in cases.items():
        assert parse_polymarket(q, close) == want, q
    # BoC carries no year: the market's close supplies it
    boc = ("Will the Bank of Canada make no change to the target for the overnight rate "
           "at the October interest rate announcement?")
    assert parse_polymarket(boc, close) == ("boc 2026-10", "hold")
    assert parse_polymarket(boc, None) is None
    assert parse_polymarket("Will the Fed increase interest rates by 25 bps after the "
                            "October 2026 meeting?", close) is None
    assert parse_polymarket("Will the ECB cut rates in 2026?", close) is None


def test_two_venue_bank_keeps_the_fed_gates():
    w = FedWatcher({"min_price": 0.90, "max_lead_days": 7, "agree_min": 0.85})
    kk = k("KXCBDECISIONENGLAND-26NOV05-H25", 0.89, 0.91)
    pp = p(BOE_HIKE, 0.92, 0.93, BOE_P_END)
    fired = evaluate(w, [kk, pp], now=NOW)
    assert {(s.platform, s.meeting) for s in fired} == {("kalshi", "boe 2026-11"),
                                                        ("polymarket", "boe 2026-11")}
    # lead counts to Kalshi's close at the announcement, not Polymarket's later endDate
    assert abs(fired[0].lead_days - (BOE_K_CLOSE - NOW).total_seconds() / 86400) < 0.01
    # the other venue disagreeing blocks it, exactly as for the Fed
    assert evaluate(w, [k("KXCBDECISIONENGLAND-26NOV05-H25", 0.60, 0.62), pp], now=NOW) == []
    # a Kalshi market that exists without a quote blocks it too: not "single venue"
    assert evaluate(w, [k("KXCBDECISIONENGLAND-26NOV05-H25", None, None), pp], now=NOW) == []


def test_single_venue_only_without_any_kalshi_market_and_past_the_lead_floor():
    w = FedWatcher({"min_price": 0.90, "max_lead_days": 7, "single_venue_min_lead_days": 1.5})
    pp = p(RBA_HOLD, 0.94, 0.95, RBA_P_END)
    (s,) = evaluate(w, [pp], now=NOW)
    assert (s.meeting, s.bucket, s.other_mid) == ("rba 2026-11", "hold", None)
    # inside the floor: Polymarket's endDate may already be past the decision
    assert evaluate(w, [pp], now=RBA_P_END - timedelta(days=1)) == []
    # below the price gate and outside the window: nothing
    assert evaluate(w, [p(RBA_HOLD, 0.85, 0.88, RBA_P_END)], now=NOW) == []
    assert evaluate(w, [pp], now=RBA_P_END - timedelta(days=9)) == []


def test_fed_watcher_default_still_requires_the_other_venue():
    w = FedWatcher({})
    q = "Will the Fed increase interest rates by 25 bps after the November 2026 meeting?"
    assert w.evaluate([p(q, 0.95, 0.96, BOE_P_END)], now=NOW) == []


def test_record_settle_and_score_by_gate(tmp_path):
    conn = sqlite3.connect(":memory:")
    w = FedWatcher({})
    two = evaluate(w, [k("KXCBDECISIONENGLAND-26NOV05-H25", 0.91, 0.92),
                       p(BOE_HIKE, 0.92, 0.93, BOE_P_END)], now=NOW)
    one = evaluate(w, [p(RBA_HOLD, 0.94, 0.95, RBA_P_END, token="rba-yes")], now=NOW)
    assert record(conn, two + one) == 3
    assert record(conn, two + one) == 0                       # first fire only
    assert open_poly_tokens(conn) == ["rba-yes"]
    # Kalshi settles the BoE pair; Polymarket's own resolution settles the RBA row
    assert settle(conn, {"KXCBDECISIONENGLAND-26NOV05-H25": "yes", "rba-yes": "no"}) == 3
    tv, sv = scorecard(conn, "two_venue"), scorecard(conn, "single_venue")
    assert (tv["fired"], tv["settled"], tv["paid"]) == (2, 2, 2)
    assert tv["surprise_upper95"] == 1.5                        # rule of three on n = 2
    assert (sv["fired"], sv["paid"], sv["mean_net_return"]) == (1, 0, -1.0)
    out = tmp_path / "cb.csv"
    assert export_csv(conn, out) == 3
    assert out.read_text().splitlines()[0] == ",".join(EXPORT_FIELDS)


def test_a_failed_kalshi_read_drops_the_bank_instead_of_going_single_venue():
    """A 429 on one series must not turn that bank's Polymarket buckets into
    single-venue fires: the bank is skipped for the cycle."""
    from src.cb_watch import fetch

    class Kalshi:
        def series_markets(self, series):
            if series == "KXCBDECISIONENGLAND":
                raise RuntimeError("429 Too Many Requests")
            return []

        def series_results(self, series):
            return {}

    class Poly:
        def tagged_markets(self, tag):
            return [p(BOE_HIKE, 0.95, 0.96, BOE_P_END, token="boe"),
                    p(RBA_HOLD, 0.94, 0.95, RBA_P_END, token="rba")] if tag == "Global-Rates" else []

    markets, _ = fetch(Kalshi(), Poly(), sleep=lambda s: None)
    assert [m.market_id for m in markets] == ["rba"]
    w = FedWatcher({})
    assert {s.meeting for s in evaluate(w, markets, now=NOW)} == {"rba 2026-11"}
