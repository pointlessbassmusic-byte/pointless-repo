"""MLB start cross-check: a venue clock that runs late (Kalshi's ticker
time for a rescheduled game) is pulled to MLB's scheduled first pitch;
never pushed later, unmatched or non-baseball markets untouched."""
from datetime import datetime, timedelta, timezone

from sportsbot.bot.start_check import earliest_starts
from sportsbot.core.types import Exchange, MarketInfo, Sport
from sportsbot.data.mlb_data import UpcomingGame

T = datetime(2026, 10, 13, 0, 0, tzinfo=timezone.utc)     # ticker's 20:00 ET


def _m(home, away, start, sport=Sport.BASEBALL, mid="K1"):
    return MarketInfo(exchange=Exchange.KALSHI, market_id=mid, question="", sport=sport,
                      home=home, away=away, start_time=start)


def _g(home, away, when, status="Preview"):
    return UpcomingGame(game_id=1, date=when, home=home, away=away,
                        meta={"status": status})


def test_a_late_ticker_is_pulled_to_the_scheduled_first_pitch():
    # LAD@MIL 2026-10-12: ticker 20:00 ET, first pitch 21:00 UTC (3 h earlier)
    games = [_g("milwaukee brewers", "los angeles dodgers", T - timedelta(hours=3))]
    (m,), moved = earliest_starts(
        [_m("milwaukee brewers", "los angeles dodgers", T)], games, now=T - timedelta(hours=5))
    assert moved == 1 and m.start_time == T - timedelta(hours=3)
    # the pairing is order-free (Polymarket's outcomes[0] need not be home)
    (m,), _ = earliest_starts(
        [_m("los angeles dodgers", "milwaukee brewers", T)], games, now=T)
    assert m.start_time == T - timedelta(hours=3)


def test_a_live_game_counts_as_started_by_now():
    now = T - timedelta(hours=1)
    games = [_g("milwaukee brewers", "los angeles dodgers", T + timedelta(hours=1), "Live")]
    (m,), moved = earliest_starts([_m("milwaukee brewers", "los angeles dodgers", T)],
                                  games, now=now)
    assert moved == 1 and m.start_time == now


def test_never_later_and_unmatched_or_other_sports_untouched():
    later = [_g("milwaukee brewers", "los angeles dodgers", T + timedelta(minutes=30))]
    (m,), moved = earliest_starts([_m("milwaukee brewers", "los angeles dodgers", T)],
                                  later, now=T - timedelta(hours=2))
    assert moved == 0 and m.start_time == T
    other = [_g("boston red sox", "chicago cubs", T - timedelta(hours=3))]
    hockey = _m("milwaukee brewers", "los angeles dodgers", T, sport=Sport.HOCKEY, mid="H")
    out, moved = earliest_starts([_m("milwaukee brewers", "los angeles dodgers", T), hockey],
                                 other + [_g("milwaukee brewers", "los angeles dodgers",
                                             T - timedelta(days=2))], now=T)
    assert moved == 0 and [x.start_time for x in out] == [T, T]   # 2 days off: another game
    none = _m("milwaukee brewers", "los angeles dodgers", None)
    assert earliest_starts([none], later, now=T)[0][0].start_time is None
