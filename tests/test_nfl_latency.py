"""NFL in-play study: scoring plays from ESPN's cumulative scores stamped
at the snap, nickname matching, and the residual read at snap + lag."""
from datetime import datetime, timezone

from sportsbot.backtest.nfl_latency import LAGS, match, run, scoring_plays
from sportsbot.backtest.polymarket_market import PMGame
from sportsbot.backtest.wallet_follow import Trade

T0 = 1_792_370_451


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _p(a, h, ts, kind="Pass"):
    return {"awayScore": a, "homeScore": h, "wallclock": _iso(ts), "type": {"text": kind},
            "period": {"number": 1}}


EV = {"id": "1", "ts": T0 - 1200, "home": "packers", "away": "cowboys"}


def test_scoring_plays_from_score_changes_stamped_at_the_snap():
    summ = {"drives": {"previous": [
        {"plays": [_p(0, 0, T0 - 40), _p(0, 6, T0, "Passing Touchdown"), _p(0, 7, T0 + 60, "Extra Point")]},
        {"plays": [_p(3, 7, T0 + 400, "Field Goal Good"), {"text": "timeout"}]}]}}
    plays = scoring_plays(summ, EV)
    assert [(p.scorer, p.runs, p.event) for p in plays] == [
        ("packers", 6, "Passing Touchdown"), ("packers", 1, "Extra Point"),
        ("cowboys", 3, "Field Goal Good")]
    assert plays[0].t_pitch == plays[0].t_feed == T0


def _g(home, away, start):
    return PMGame(date=datetime.fromtimestamp(start, timezone.utc), slug="nfl-x", home=home,
                  away=away, token="t", start_ts=start, close_ts=start + 99_999, home_won=True,
                  volume=1.0, condition_id="c")


def test_match_on_nicknames_in_either_order_and_residual_after_the_snap():
    assert match(_g("cowboys", "packers", T0 - 1500), [EV]) == EV
    assert match(_g("cowboys", "bears", T0 - 1500), [EV]) is None
    # outcomes[0] = cowboys; the packers score, so their side is 1 - p_home
    tape = ([Trade(T0 - 100 + i, "w", 1, 0.40, 10) for i in range(5)]
            + [Trade(T0 + 9, "w", -1, 0.36, 10), Trade(T0 + 25, "w", -1, 0.35, 10)]
            + [Trade(T0 + 120 + i, "w", -1, 0.35, 10) for i in range(5)])
    (play,) = scoring_plays({"drives": {"previous": [{"plays": [_p(0, 7, T0, "Touchdown")]}]}}, EV)
    (r,) = run([_g("cowboys", "packers", T0 - 1500)], {"c": tape}, {"c": [play]})
    assert abs(r["jump"] - 0.05) < 1e-9 and r["half_vs_pitch"] == 9
    assert set(r["resid"]) == set(LAGS)
    assert r["resid"][10.0] < 0       # by snap + 10 s the taker already pays the moved price
