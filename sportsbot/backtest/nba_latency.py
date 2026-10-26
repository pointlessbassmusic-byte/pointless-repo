"""NBA in-play: how fast does Polymarket price a basket, and is anything left
for a taker reading the play-by-play? The NBA counterpart of Results 23/25
(candidate queue item 4, the last unmeasured league).

ESPN's NBA summary carries a flat ``plays`` list, each play stamped with a
``wallclock`` and the cumulative score. Every made basket or free throw is a
scoring play. The stamp is the basket itself: on hand-checked late plays
the price jumps 2-3 s after it.

The MLB/NFL rule (keep plays whose price moved >= 4c toward the scorer
over the next 1-4 min) cannot be used here: a team scores every ~20 s, so
that filter selects the scorer's future run and reads +3c to +6c at every
lag on this data. Every scoring play is scored instead, with no filter on
what came next. Data only.
"""

from __future__ import annotations

import logging
import statistics
from datetime import timedelta

from sportsbot.backtest import nfl_latency as nl
from sportsbot.backtest.polymarket_market import PMGame
from sportsbot.backtest.score_latency import ScoringPlay
from sportsbot.backtest.wallet_follow import Trade, taker_fee
from sportsbot.signals.sharp import cluster_bootstrap_mean

log = logging.getLogger(__name__)

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
LAGS = (0.0, 2.0, 5.0, 10.0, 20.0)
MARK = (60.0, 180.0)


def scoring_plays(summary: dict, event: dict) -> list[ScoringPlay]:
    """Plays where a score rose, stamped at ESPN's ``wallclock``."""
    out, away, home = [], 0, 0
    for p in summary.get("plays") or []:
        a, h = p.get("awayScore"), p.get("homeScore")
        if a is None or h is None:
            continue
        da, dh = a - away, h - home
        away, home = a, h
        if (da <= 0 and dh <= 0) or not p.get("wallclock"):
            continue
        w = nl._iso(p["wallclock"])
        out.append(ScoringPlay(t_pitch=w, t_feed=w,
                               scorer=event["home"] if dh > 0 else event["away"],
                               runs=max(da, dh), inning=int((p.get("period") or {}).get("number", 0)),
                               event=str((p.get("type") or {}).get("text", ""))))
    return out


def _side(t: Trade, sign: int) -> float:
    return t.p_home if sign > 0 else 1.0 - t.p_home


def taker_rows(games: list[PMGame], tapes: dict[str, list[Trade]],
               plays: dict[str, list[ScoringPlay]], lags: tuple = LAGS,
               fee_rate: float = 0.03) -> list[dict]:
    """Every scoring play, no filter on what the price did next: a taker
    buys the scorer's side at the first scoring-side print at or after
    stamp + L. Marked to the median print 60-180 s later and to the
    settlement. ``late_mid`` (4th quarter or OT, pre-play price 0.15-0.85)
    uses only what is known at the decision."""
    rows = []
    for g in games:
        tape = tapes.get(g.condition_id) or []
        for play in plays.get(g.condition_id) or []:
            if play.scorer not in (g.home, g.away):
                continue
            sign = 1 if play.scorer == g.home else -1
            pre = [_side(t, sign) for t in tape if play.t_feed - 60 <= t.ts < play.t_feed - 1]
            if len(pre) < 3:
                continue
            p0 = statistics.median(pre[-5:])
            won = 1.0 if g.home_won == (sign > 0) else 0.0
            out = {}
            for lag in lags:
                q = next((_side(t, sign) for t in tape
                          if t.ts >= play.t_feed + lag and t.direction == sign), None)
                if q is None or q >= 0.99:
                    continue
                post = [_side(t, sign) for t in tape
                        if play.t_feed + lag + MARK[0] <= t.ts <= play.t_feed + lag + MARK[1]]
                if len(post) < 3:
                    continue
                f = taker_fee(q, fee_rate)
                out[lag] = (statistics.median(post) - q - f, won - q - f)
            if out:
                rows.append({"game": g.condition_id, "late_mid": play.inning >= 4 and 0.15 <= p0 <= 0.85,
                             "lag": out})
    return rows


def summarise(rows: list[dict], lags: tuple = LAGS) -> dict:
    res = {}
    for name, keep in (("all", lambda r: True), ("late_mid", lambda r: r["late_mid"])):
        for lag in lags:
            have = [r for r in rows if keep(r) and lag in r["lag"]]
            if len(have) < 5:
                continue
            cl = [r["game"] for r in have]
            res[(name, lag)] = (cluster_bootstrap_mean([r["lag"][lag][0] for r in have], cl),
                                cluster_bootstrap_mean([r["lag"][lag][1] for r in have], cl))
    return res


def format_report(rows: list[dict], n_games: int) -> str:
    if not rows:
        return f"{n_games} games matched; no scoring play had a tape to score."
    s = summarise(rows)
    lines = [f"NBA in-play taker, Polymarket vs ESPN play-by-play: {len(rows)} scoring plays, "
             f"{len({r['game'] for r in rows})} games ({n_games} matched)",
             "  buy the scorer's side at the first scoring-side print >= stamp + L, net of the "
             "0.03 fee, per $ (game-clustered 95% CI)"]
    for (name, lag), (mk, st) in s.items():
        lines.append(f"    {name:<8} +{lag:>3.0f}s  n {mk['n']:5d}  markout {mk['mean']:+.4f} "
                     f"[{mk['lo']:+.4f}, {mk['hi']:+.4f}]  settle {st['mean']:+.4f} "
                     f"[{st['lo']:+.4f}, {st['hi']:+.4f}]")
    return "\n".join(lines)


def collect(days: int = 14, max_games: int = 200, http=None):
    """(games, tapes, plays) for resolved Polymarket NBA games matched to ESPN."""
    import httpx

    from sportsbot.backtest import wallet_follow as wf
    from sportsbot.backtest.polymarket_market import fetch_resolved

    http = http or httpx.Client(timeout=30)
    games = [g for g in fetch_resolved("basketball", days=days)
             if g.condition_id and g.slug.startswith("nba-")]
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
                    boards[k] = nl.scoreboard(day, http, base=ESPN)
                except Exception as exc:  # noqa: BLE001
                    log.warning("scoreboard %s failed: %s", k, exc)
                    boards[k] = []
            evs += boards[k]
        ev = nl.match(g, evs)
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
