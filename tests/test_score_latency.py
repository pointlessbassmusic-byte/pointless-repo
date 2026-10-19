"""Score-feed latency study: scoring plays from the feed, the venue's
reaction time against the feed stamp, the taker residual and its fee,
and conservative game matching."""
from datetime import datetime, timezone

from sportsbot.backtest.polymarket_market import PMGame
from sportsbot.backtest.score_latency import (
    ScoringPlay, match, reaction, scoring_plays, summarise,
)
from sportsbot.backtest.wallet_follow import Trade, taker_fee

T0 = 1_791_420_000


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def _play(away, home, half, end, pitch, event="Single"):
    return {"result": {"event": event, "awayScore": away, "homeScore": home},
            "about": {"endTime": _iso(end), "halfInning": half, "inning": 3},
            "playEvents": [{"startTime": _iso(pitch - 20)}, {"startTime": _iso(pitch)}]}


def test_scoring_plays_take_runs_from_the_score_change_and_the_batting_side():
    feed = {"gameData": {"teams": {"home": {"name": "Atlanta Braves"},
                                   "away": {"name": "Los Angeles Dodgers"}}},
            "liveData": {"plays": {"allPlays": [
                _play(0, 0, "top", T0 + 10, T0),                       # no run
                _play(0, 2, "bottom", T0 + 130, T0 + 100, "Home Run"),
                _play(1, 2, "top", T0 + 330, T0 + 300),
            ]}}}
    plays = scoring_plays(feed)
    assert [(p.scorer, p.runs, p.event) for p in plays] == [
        ("atlanta braves", 2, "Home Run"), ("los angeles dodgers", 1, "Single")]
    assert (plays[0].t_pitch, plays[0].t_feed) == (T0 + 100, T0 + 130)


def _tr(ts, p, d=1):
    return Trade(ts=ts, wallet="w", direction=d, p_home=p, size=10.0)


PLAY = ScoringPlay(t_pitch=T0, t_feed=T0 + 15, scorer="x", runs=1, inning=5, event="Home Run")


def test_venue_first_when_half_the_jump_prints_before_the_feed_stamp():
    tape = ([_tr(T0 - 100 + i, 0.50) for i in range(5)]
            + [_tr(T0 + 5, 0.58), _tr(T0 + 20, 0.60)]
            + [_tr(T0 + 15 + 90 + i, 0.60) for i in range(5)])
    r = reaction(tape, PLAY, sign=1)
    assert abs(r["jump"] - 0.10) < 1e-9
    assert (r["half_vs_pitch"], r["half_vs_feed"]) == (5, -10)
    # a taker at the feed stamp lifts 0.60 and is left with minus the fee
    assert abs(r["resid"][0.0] - (0.0 - taker_fee(0.60))) < 1e-12
    s = summarise([{**r, "game": "g"}])
    assert s["venue_first"] == 1.0 and s["venue_first_5s"] == 1.0


def test_away_frame_and_thin_or_flat_tapes():
    # sign -1: the scorer is outcomes[1], so its price is 1 - p_home
    tape = ([_tr(T0 - 100 + i, 0.50, -1) for i in range(5)]
            + [_tr(T0 + 40, 0.40, -1)]
            + [_tr(T0 + 120 + i, 0.40, -1) for i in range(5)])
    r = reaction(tape, PLAY, sign=-1)
    assert abs(r["jump"] - 0.10) < 1e-9 and r["half_vs_feed"] == 25
    assert reaction(tape, PLAY, sign=1) is None                 # moved against the scorer
    assert reaction(tape[:2] + tape[5:], PLAY, sign=-1) is None  # too few pre prints
    flat = [_tr(T0 - 100 + i, 0.50) for i in range(5)] + [_tr(T0 + 120 + i, 0.52) for i in range(5)]
    assert reaction(flat, PLAY, sign=1) is None                 # under min_jump


def _pm(home, away, start):
    return PMGame(date=datetime.fromtimestamp(start, timezone.utc), slug="s", home=home,
                  away=away, token="t", start_ts=start, close_ts=start + 9 * 3600,
                  home_won=True, volume=1.0)


def test_match_needs_both_teams_and_picks_the_nearer_doubleheader_game():
    sched = [{"pk": 1, "ts": T0, "home": "atlanta braves", "away": "los angeles dodgers"},
             {"pk": 2, "ts": T0 + 5 * 3600, "home": "atlanta braves", "away": "los angeles dodgers"},
             {"pk": 3, "ts": T0, "home": "new york yankees", "away": "tampa bay rays"}]
    # Polymarket's outcomes[0] need not be the MLB home side
    assert match(_pm("los angeles dodgers", "atlanta braves", T0 + 4 * 3600), sched) == 2
    assert match(_pm("atlanta braves", "los angeles dodgers", T0 - 600), sched) == 1
    assert match(_pm("atlanta braves", "los angeles dodgers", T0 + 30 * 3600), sched) is None
    assert match(_pm("atlanta braves", "new york mets", T0), sched) is None
