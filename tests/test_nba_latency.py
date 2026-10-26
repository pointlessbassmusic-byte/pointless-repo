"""NBA in-play study: scoring plays from ESPN's flat play list, and the
taker scored on every play with no filter on what the price did next."""
from datetime import datetime, timezone

from sportsbot.backtest.nba_latency import taker_rows, scoring_plays
from sportsbot.backtest.polymarket_market import PMGame
from sportsbot.backtest.wallet_follow import Trade

T0 = 1_792_370_451
EV = {"id": "1", "ts": T0 - 9000, "home": "76ers", "away": "cavaliers"}


def _p(a, h, ts, q=4, text="Jump Shot"):
    return {"awayScore": a, "homeScore": h, "period": {"number": q}, "type": {"text": text},
            "wallclock": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def test_scoring_plays_from_the_flat_play_list():
    plays = scoring_plays({"plays": [_p(0, 0, T0 - 30), _p(0, 2, T0), _p(3, 2, T0 + 20),
                                     {"text": "timeout"}, _p(3, 2, T0 + 40)]}, EV)
    assert [(p.scorer, p.runs, p.t_feed) for p in plays] == [("76ers", 2, T0),
                                                             ("cavaliers", 3, T0 + 20)]


def test_taker_pays_the_moved_price_and_is_marked_without_selection():
    g = PMGame(date=datetime.fromtimestamp(T0, timezone.utc), slug="nba-x", home="76ers",
               away="cavaliers", token="t", start_ts=T0 - 9000, close_ts=T0 + 9000,
               home_won=False, volume=1.0, condition_id="c")
    tape = ([Trade(T0 - 30 + i, "w", 1, 0.50, 10) for i in range(5)]
            + [Trade(T0 + 1, "w", 1, 0.52, 10), Trade(T0 + 6, "w", 1, 0.56, 10)]
            + [Trade(T0 + 100 + i, "w", -1, 0.55, 10) for i in range(5)])
    (play,) = scoring_plays({"plays": [_p(0, 2, T0)]}, EV)
    (r,) = taker_rows([g], {"c": tape}, {"c": [play]}, lags=(0.0, 5.0))
    assert r["late_mid"] is True
    mk0, st0 = r["lag"][0.0]
    # bought at 0.52 at +1 s, marked at 0.55; the 76ers lost, so settlement pays 0
    assert mk0 > 0 and st0 < -0.5
    assert r["lag"][5.0][0] < 0          # by +5 s the taker pays 0.56 for a 0.55 mark
