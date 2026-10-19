"""Score-feed latency: does Polymarket's in-play MLB price move before the
official feed records a run, and is anything left for a feed-driven taker?

Candidate queue item 4 (docs/MASTER_PLAN.md): measure before any in-play
code. For every run-scoring play in a resolved Polymarket MLB moneyline:

- ``t_pitch``: start of the play's last pitch (the ball is in play
  within a second of it).
- ``t_feed``: the play's ``about.endTime``. The GUMBO archive
  (``feed/live/timestamps``) first carries the new score at exactly this
  stamp (checked by hand on the 2026-10-07 LAD@ATL game: +14 s, +11 s and
  +27 s after the pitch for its three scoring plays, matching endTime to
  the second), so it is the EARLIEST a feed consumer could know. Real
  delivery (CDN, polling) is later, which only flatters the feed.
- pre / post: median taker print in the 3 min before the pitch / in
  1-4 min after ``t_feed``, in the scoring team's frame. ``jump`` =
  post - pre; only plays that moved the price by ``min_jump`` count.
- ``t_half``: first print at or after the pitch that has covered half the
  jump. ``t_half - t_feed`` < 0 means the venue moved first.
- residual at lag L: ``post`` minus the first print on the scoring side
  (a taker lifting the ask) at least L seconds after ``t_feed``, minus the
  taker fee: what a bot reading the feed instantly could still earn.

The tape's timestamps are settlement times, a few seconds after the match,
so venue times are late if anything: a venue lead survives that bias.
Data only.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sportsbot.backtest.polymarket_market import PMGame
from sportsbot.backtest.wallet_follow import Trade, taker_fee
from sportsbot.signals.sharp import cluster_bootstrap_mean

log = logging.getLogger(__name__)

STATSAPI = "https://statsapi.mlb.com/api"
FEED_CACHE = "data/cache/mlb_feeds"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
LAGS = (0.0, 5.0, 15.0, 30.0)
PRE_WINDOW = 180.0
POST_WINDOW = (60.0, 240.0)


@dataclass
class ScoringPlay:
    t_pitch: float
    t_feed: float
    scorer: str          # MLB full team name, lower case
    runs: int
    inning: int
    event: str


def _iso(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def scoring_plays(feed: dict) -> list[ScoringPlay]:
    """Run-scoring plays from a (trimmed) GUMBO feed, with pitch and feed times."""
    teams = feed["gameData"]["teams"]
    names = {"top": teams["away"]["name"].lower(), "bottom": teams["home"]["name"].lower()}
    out, away, home = [], 0, 0
    for p in feed["liveData"]["plays"]["allPlays"]:
        res, ab = p.get("result", {}), p.get("about", {})
        a, h = res.get("awayScore", away), res.get("homeScore", home)
        runs = (a - away) + (h - home)
        away, home = a, h
        if runs <= 0 or not ab.get("endTime"):
            continue
        evs = [e for e in p.get("playEvents") or [] if e.get("startTime")]
        if not evs:
            continue
        # the batting side scores (a balk or wild pitch still credits it)
        out.append(ScoringPlay(t_pitch=_iso(evs[-1]["startTime"]), t_feed=_iso(ab["endTime"]),
                               scorer=names[ab.get("halfInning", "top")], runs=runs,
                               inning=int(ab.get("inning", 0)), event=str(res.get("event", ""))))
    return out


def _side(t: Trade, sign: int) -> float:
    return t.p_home if sign > 0 else 1.0 - t.p_home


def reaction(trades: list[Trade], play: ScoringPlay, sign: int, min_jump: float = 0.04,
             fee_rate: float = 0.05, lags: tuple = LAGS) -> Optional[dict]:
    """One play's venue reaction. ``sign`` = +1 when the scorer is the
    tape's home frame (outcomes[0]). None when the tape is too thin or the
    price did not move ``min_jump`` toward the scorer."""
    pre = [_side(t, sign) for t in trades
           if play.t_pitch - PRE_WINDOW <= t.ts < play.t_pitch - 1]
    post = [_side(t, sign) for t in trades
            if play.t_feed + POST_WINDOW[0] <= t.ts <= play.t_feed + POST_WINDOW[1]]
    if len(pre) < 3 or len(post) < 3:
        return None
    p0, p1 = statistics.median(pre[-5:]), statistics.median(post)
    jump = p1 - p0
    if jump < min_jump:
        return None
    half = next((t.ts for t in trades if t.ts >= play.t_pitch - 1
                 and _side(t, sign) >= p0 + jump / 2), None)
    if half is None or half > play.t_feed + POST_WINDOW[1]:
        return None
    resid = {}
    for lag in lags:
        q = next((_side(t, sign) for t in trades
                  if t.ts >= play.t_feed + lag and t.direction == sign), None)
        if q is not None and q < 1.0:
            resid[lag] = p1 - q - taker_fee(q, fee_rate)
    return {"pre": p0, "post": p1, "jump": jump, "half_vs_feed": half - play.t_feed,
            "half_vs_pitch": half - play.t_pitch, "feed_vs_pitch": play.t_feed - play.t_pitch,
            "resid": resid, "event": play.event, "inning": play.inning}


# ---------------------------------------------------------------------------
# Fetching (cached) and matching
# ---------------------------------------------------------------------------
def schedule(start: datetime, end: datetime, http) -> list[dict]:
    r = http.get(f"{STATSAPI}/v1/schedule", params={
        "sportId": 1, "startDate": start.strftime("%Y-%m-%d"),
        "endDate": end.strftime("%Y-%m-%d")})
    r.raise_for_status()
    return [{"pk": g["gamePk"], "ts": _iso(g["gameDate"]),
             "home": g["teams"]["home"]["team"]["name"].lower(),
             "away": g["teams"]["away"]["team"]["name"].lower()}
            for d in r.json().get("dates", []) for g in d["games"]
            if g.get("status", {}).get("abstractGameState") == "Final"]


def match(game: PMGame, sched: list[dict], tol_h: float = 6.0) -> Optional[int]:
    """gamePk for a Polymarket game: same two teams, nearest first pitch
    within ``tol_h``. A doubleheader resolves to the nearer start; no match
    is skipped, never guessed."""
    teams = {game.home, game.away}
    c = [g for g in sched if {g["home"], g["away"]} == teams
         and abs(g["ts"] - game.start_ts) <= tol_h * 3600]
    return min(c, key=lambda g: abs(g["ts"] - game.start_ts))["pk"] if c else None


def _trim(feed: dict) -> dict:
    keep = []
    for p in feed["liveData"]["plays"]["allPlays"]:
        evs = [e for e in p.get("playEvents") or [] if e.get("startTime")]
        keep.append({"result": {k: p.get("result", {}).get(k)
                                for k in ("event", "awayScore", "homeScore")},
                     "about": {k: p.get("about", {}).get(k)
                               for k in ("endTime", "halfInning", "inning")},
                     "playEvents": [{"startTime": evs[-1]["startTime"]}] if evs else []})
    t = feed["gameData"]["teams"]
    return {"gameData": {"teams": {s: {"name": t[s]["name"]} for s in ("home", "away")}},
            "liveData": {"plays": {"allPlays": keep}}}


def fetch_feed(pk: int, http, pause: float = 0.3) -> Optional[dict]:
    os.makedirs(FEED_CACHE, exist_ok=True)
    path = os.path.join(FEED_CACHE, f"{pk}.json")
    if os.path.exists(path):
        try:
            with open(path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    try:
        r = http.get(f"{STATSAPI}/v1.1/game/{pk}/feed/live")
        r.raise_for_status()
        feed = _trim(r.json())
    except Exception as exc:  # noqa: BLE001 — one game is not fatal
        log.warning("feed %s failed: %s", pk, exc)
        return None
    with open(path, "w") as fh:
        json.dump(feed, fh)
    time.sleep(pause)
    return feed


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def run(games: list[PMGame], tapes: dict[str, list[Trade]], feeds: dict[str, dict],
        min_jump: float = 0.04, fee_rate: float = 0.05) -> list[dict]:
    rows = []
    for g in games:
        feed, tape = feeds.get(g.condition_id), tapes.get(g.condition_id) or []
        if not feed or not tape:
            continue
        for play in scoring_plays(feed):
            if play.scorer not in (g.home, g.away):
                continue
            r = reaction(tape, play, 1 if play.scorer == g.home else -1, min_jump, fee_rate)
            if r:
                rows.append({**r, "game": g.condition_id})
    return rows


def summarise(rows: list[dict], lags: tuple = LAGS) -> dict:
    if not rows:
        return {"n": 0}
    d = [r["half_vs_feed"] for r in rows]
    out = {"n": len(rows), "games": len({r["game"] for r in rows}),
           "jump_med": statistics.median(r["jump"] for r in rows),
           "feed_vs_pitch_med": statistics.median(r["feed_vs_pitch"] for r in rows),
           "half_vs_pitch_med": statistics.median(r["half_vs_pitch"] for r in rows),
           "half_vs_feed_med": statistics.median(d),
           "venue_first": sum(x < 0 for x in d) / len(d),
           "venue_first_5s": sum(x <= -5 for x in d) / len(d), "resid": {}}
    for lag in lags:
        have = [r for r in rows if lag in r["resid"]]
        if len(have) >= 5:
            out["resid"][lag] = cluster_bootstrap_mean(
                [r["resid"][lag] for r in have], [r["game"] for r in have])
    return out


def format_report(rows: list[dict], n_games: int) -> str:
    s = summarise(rows)
    if not s["n"]:
        return f"{n_games} games matched; no scoring play moved the price enough to measure."
    lines = [
        f"Score-feed latency, Polymarket MLB in-play: {s['n']} scoring plays that moved "
        f"the price >= 4c, {s['games']} games ({n_games} matched)",
        f"  median jump {s['jump_med']:+.3f}; feed stamp {s['feed_vs_pitch_med']:.0f}s after the pitch",
        f"  half the move printed {s['half_vs_pitch_med']:.0f}s after the pitch, "
        f"{s['half_vs_feed_med']:+.0f}s vs the feed stamp (median)",
        f"  venue moved half-way BEFORE the feed stamp on {s['venue_first']:.0%} of plays "
        f"(>= 5 s before: {s['venue_first_5s']:.0%})",
        "  left for a taker reading the feed (post - first scoring-side print after "
        "feed + lag - fee, per $ of price; game-clustered 95% CI):"]
    for lag, ci in s["resid"].items():
        if ci and ci["lo"] is not None:
            lines.append(f"    lag {lag:>4.0f}s: {ci['mean']:+.4f}  [{ci['lo']:+.4f}, {ci['hi']:+.4f}]  "
                         f"n {ci['n']}")
    return "\n".join(lines)
