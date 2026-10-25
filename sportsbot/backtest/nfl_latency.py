"""NFL in-play: how fast does Polymarket price a score, and is anything left
for a taker reading the play-by-play? The NFL counterpart of Result 23.

ESPN's game summary (`site.api.espn.com/.../football/nfl/summary`) stamps
every play with a ``wallclock``. On the 2026-10-19 DAL@GB game the stamps
sit a median ~40 s apart and the price reacts 6-12 s AFTER the stamp of a
9-yard touchdown pass, so the stamp is the SNAP, not the moment the score
is known or published. A feed reader learns the result only after the play
ends and is entered, so the residual is read at snap + 10/20/30/60 s; the
earliest a feed could carry a score is a few seconds after the play ends.

Scoring plays come from the cumulative score on each play (a touchdown and
its extra point are separate plays; the extra point rarely moves the price
4c and drops out). The scorer is the side whose score rose. Prices and the
residual rule are `score_latency.reaction`'s. Data only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sportsbot.backtest.polymarket_market import PMGame
from sportsbot.backtest.score_latency import ScoringPlay, reaction, summarise
from sportsbot.backtest.wallet_follow import Trade

log = logging.getLogger(__name__)

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
LAGS = (10.0, 20.0, 30.0, 60.0)


def _iso(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def scoreboard(day: datetime, http, base: str = ESPN) -> list[dict]:
    r = http.get(f"{base}/scoreboard", params={"dates": day.strftime("%Y%m%d")})
    r.raise_for_status()
    out = []
    for e in r.json().get("events", []):
        comp = (e.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): str(c.get("team", {}).get("name", "")).lower()
                 for c in comp.get("competitors", [])}
        if "home" in teams and "away" in teams:
            out.append({"id": e["id"], "ts": _iso(e["date"]), "home": teams["home"],
                        "away": teams["away"]})
    return out


def match(game: PMGame, events: list[dict], tol_h: float = 6.0) -> Optional[dict]:
    """ESPN event for a Polymarket game: both nicknames, nearest kickoff."""
    c = [e for e in events if {e["home"], e["away"]} == {game.home, game.away}
         and abs(e["ts"] - game.start_ts) <= tol_h * 3600]
    return min(c, key=lambda e: abs(e["ts"] - game.start_ts)) if c else None


def scoring_plays(summary: dict, event: dict) -> list[ScoringPlay]:
    """Plays where a score rose, stamped at the snap (``wallclock``)."""
    out, away, home = [], 0, 0
    for drive in (summary.get("drives") or {}).get("previous") or []:
        for p in drive.get("plays") or []:
            a, h = p.get("awayScore"), p.get("homeScore")
            if a is None or h is None:
                continue
            da, dh = a - away, h - home
            away, home = a, h
            if (da <= 0 and dh <= 0) or not p.get("wallclock"):
                continue
            w = _iso(p["wallclock"])
            out.append(ScoringPlay(t_pitch=w, t_feed=w,
                                   scorer=event["home"] if dh > 0 else event["away"],
                                   runs=max(da, dh), inning=int((p.get("period") or {}).get("number", 0)),
                                   event=str((p.get("type") or {}).get("text", ""))))
    return out


def run(games: list[PMGame], tapes: dict[str, list[Trade]], plays: dict[str, list[ScoringPlay]],
        min_jump: float = 0.04, fee_rate: float = 0.03) -> list[dict]:
    rows = []
    for g in games:
        tape = tapes.get(g.condition_id) or []
        for play in plays.get(g.condition_id) or []:
            if play.scorer not in (g.home, g.away):
                continue
            r = reaction(tape, play, 1 if play.scorer == g.home else -1, min_jump, fee_rate,
                         lags=LAGS)
            if r:
                rows.append({**r, "game": g.condition_id})
    return rows


def format_report(rows: list[dict], n_games: int) -> str:
    s = summarise(rows, lags=LAGS)
    if not s["n"]:
        return f"{n_games} games matched; no scoring play moved the price enough to measure."
    lines = [f"NFL in-play, Polymarket vs ESPN play-by-play: {s['n']} scoring plays that moved "
             f"the price >= 4c, {s['games']} games ({n_games} matched)",
             f"  median jump {s['jump_med']:+.3f}; half the move printed "
             f"{s['half_vs_pitch_med']:.0f}s after the snap (median)",
             "  left for a taker L s after the snap (post - first scoring-side print - "
             "0.03 fee, per $; game-clustered 95% CI):"]
    for lag, ci in s["resid"].items():
        if ci and ci["lo"] is not None:
            lines.append(f"    snap + {lag:>3.0f}s: {ci['mean']:+.4f}  [{ci['lo']:+.4f}, "
                         f"{ci['hi']:+.4f}]  n {ci['n']}")
    return "\n".join(lines)


def collect(days: int = 45, max_games: int = 200, http=None):
    """(games, tapes, plays) for resolved Polymarket NFL games matched to ESPN."""
    import httpx

    from sportsbot.backtest import wallet_follow as wf
    from sportsbot.backtest.polymarket_market import fetch_resolved

    http = http or httpx.Client(timeout=30)
    games = [g for g in fetch_resolved("football", days=days)
             if g.condition_id and g.slug.startswith("nfl-")]
    games = sorted(sorted(games, key=lambda g: -g.volume)[:max_games], key=lambda g: g.start_ts)
    boards: dict[str, list[dict]] = {}
    tapes, plays = {}, {}
    for g in games:
        evs = []
        for dd in (-1, 0):
            day = g.date + timedelta(days=dd)
            k = day.strftime("%Y%m%d")
            if k not in boards:
                try:
                    boards[k] = scoreboard(day, http)
                except Exception as exc:  # noqa: BLE001
                    log.warning("scoreboard %s failed: %s", k, exc)
                    boards[k] = []
            evs += boards[k]
        ev = match(g, evs)
        if ev is None:
            continue
        try:
            r = http.get(f"{ESPN}/summary", params={"event": ev["id"]})
            r.raise_for_status()
            plays[g.condition_id] = scoring_plays(r.json(), ev)
        except Exception as exc:  # noqa: BLE001
            log.warning("summary %s failed: %s", ev["id"], exc)
            continue
        tapes[g.condition_id] = wf.orient(wf.fetch_trades(g.condition_id), g)
    return games, tapes, plays
