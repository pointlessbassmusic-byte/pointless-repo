"""Walk-forward backtest against REAL Polymarket prices and outcomes.

The Polymarket counterpart of `kalshi_market.py`, and the one that matters
for this repo's architecture: sports trade on Polymarket. Same discipline —
walk-forward ratings, a recorded pre-game price at a chosen lead, the venue's
real fee, the market's own resolution.

What Polymarket gives, and what it does not:

* `/prices-history?interval=max&fidelity=60` returns hourly trade prices for
  a token, resolved markets included. It is a price series, not a book —
  there is no bid/ask, so the spread is modelled from what the live book
  shows (one tick on every moneyline sampled) rather than recorded.
* Resolution comes from Gamma's `outcomePrices` (["1","0"] / ["0","1"]);
  ["0.5","0.5"] is a void and is skipped.
* MLB markets carry a reliable `gameStartTime`. Tennis's is a scheduled slot
  the match sometimes closes BEFORE (6% of markets); those are skipped and
  the rest anchor on it. `closedTime` is NOT a usable anchor: it trails the
  start by a median 14 hours (resolution lag), so "closedTime minus three
  hours", the Kalshi tennis anchor, lands after most Polymarket matches.
* Fees are taker-only: fee = C x 0.05 x p x (1 - p) per the documentation's
  "Sports Market Fees" page (100 shares at 0.50 -> $1.25), makers pay nothing
  and earn a 15% rebate. Gamma's raw takerBaseFee=1000 is not the formula.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sportsbot.backtest.kalshi_market import Bet, Result

log = logging.getLogger(__name__)

POST_RESULT_RAIL = 0.03    # closing prints outside (rail, 1-rail) are refused
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
CACHE_DIR = "data/cache/polymarket_prices"
SPORT_TAGS = {"baseball": 100381, "tennis": 864}


@dataclass
class PMGame:
    date: datetime
    slug: str
    home: str            # outcomes[0]; the token priced
    away: str            # outcomes[1]
    token: str           # YES token of outcomes[0]
    start_ts: int        # pre-game anchor
    close_ts: int
    home_won: bool
    volume: float


def _pj(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return []
    return v or []


def _ts(v) -> Optional[int]:
    if not v:
        return None
    s = str(v).replace(" ", "T")
    if not s.endswith("Z") and "+" not in s[10:]:
        s += "+00:00"
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _games_from_events(events, sport: str, cutoff: float,
                       out: list[PMGame], seen: set[str]) -> None:
    for ev in events:
        for m in ev.get("markets") or []:
            if m.get("sportsMarketType") != "moneyline":
                continue
            prices = _pj(m.get("outcomePrices"))
            outcomes = _pj(m.get("outcomes"))
            tokens = _pj(m.get("clobTokenIds"))
            if len(prices) != 2 or len(outcomes) != 2 or len(tokens) != 2:
                continue
            try:
                p0 = float(prices[0])
            except (TypeError, ValueError):
                continue
            if p0 not in (0.0, 1.0):
                continue                      # void / unresolved
            close_ts = _ts(m.get("closedTime")) or _ts(m.get("umaEndDate"))
            if close_ts is None or close_ts < cutoff:
                continue
            # Anchor on gameStartTime for every sport. The earlier tennis
            # anchor, closedTime minus three hours, assumed resolution
            # followed the match promptly; measured 2026-09-29 on 300
            # resolved tennis markets, closedTime trails gameStartTime by a
            # median 14 hours (p10 3.7h, p90 20.7h), so that anchor sat
            # after most matches and the "closing line" was an in-play or
            # post-result print. gameStartTime precedes closedTime on 94% of
            # tennis markets; the rest (rescheduled or closed early) are
            # skipped rather than guessed — fail closed.
            start_ts = _ts(m.get("gameStartTime"))
            if start_ts is None or start_ts >= close_ts:
                continue
            token = str(tokens[0])
            if token in seen:
                continue
            seen.add(token)
            out.append(PMGame(
                date=datetime.fromtimestamp(start_ts, timezone.utc),
                slug=m.get("slug") or ev.get("slug", ""),
                home=str(outcomes[0]).strip().lower(),
                away=str(outcomes[1]).strip().lower(),
                token=token, start_ts=start_ts, close_ts=close_ts,
                home_won=(p0 == 1.0),
                volume=float(m.get("volumeNum") or 0.0)))


def fetch_resolved(sport: str, days: int = 75, window_days: int = 3,
                   max_pages: int = 20, http=None, pause: float = 0.1,
                   end_date_lag_days: float = 8.0) -> list[PMGame]:
    """Resolved moneyline markets for a sport, one game per event
    (outcomes[0]'s token; outcomes[1] is its complement).

    Gamma rejects `offset` beyond ~2000 with a 422, and a busy MLB day lists
    dozens of derivative events per game, so a single newest-first walk
    cannot reach 75 days back. Scan `window_days`-wide endDate windows
    instead (`end_date_min`/`end_date_max`), paging within each window.
    Game events carry endDate = first pitch + 7 days (measured 2026-09-29),
    so the scan starts `end_date_lag_days` in the future and the per-market
    `closedTime` cutoff, not the window, decides what is kept."""
    import httpx

    http = http or httpx.Client(timeout=40)
    tag = SPORT_TAGS[sport]
    now = time.time()
    cutoff = now - days * 86400
    out: list[PMGame] = []
    seen: set[str] = set()
    win_end = now + end_date_lag_days * 86400
    while win_end > cutoff:
        win_start = max(cutoff, win_end - window_days * 86400)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        for page in range(max_pages):
            r = http.get(f"{GAMMA}/events", params={
                "tag_id": tag, "closed": "true",
                "end_date_min": time.strftime(fmt, time.gmtime(win_start)),
                "end_date_max": time.strftime(fmt, time.gmtime(win_end)),
                "limit": 100, "offset": page * 100})
            r.raise_for_status()
            events = r.json()
            if not events:
                break
            _games_from_events(events, sport, cutoff, out, seen)
            if len(events) < 100:
                break
            time.sleep(pause)
        win_end = win_start
    out.sort(key=lambda g: g.date)
    return out


def price_history(token: str, http=None, pause: float = 0.15) -> list[dict]:
    """Hourly (fidelity=60) trade prices for a token, cached on disk."""
    import httpx

    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{token}.json")
    if os.path.exists(path):
        try:
            with open(path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    http = http or httpx.Client(timeout=40)
    try:
        r = http.get(f"{CLOB}/prices-history",
                     params={"market": token, "interval": "max", "fidelity": 60})
        hist = r.json().get("history", []) if r.status_code == 200 else []
    except Exception as exc:  # noqa: BLE001 — one missing market is not fatal
        log.debug("history failed for %s: %s", token, exc)
        return []
    try:
        with open(path, "w") as fh:
            json.dump(hist, fh)
    except OSError:
        pass
    time.sleep(pause)
    return hist


def price_at_lead(hist: list[dict], start_ts: int, lead_hours: float,
                  spread: float = 0.01) -> Optional[tuple]:
    """(bid, ask, closing) from a trade-price series.

    The point nearest `lead_hours` before the anchor is the decision mid; a
    one-tick spread is put around it, since that is what every sampled live
    moneyline book shows and the series itself carries no book. The closing
    line is the last point strictly before the anchor. A leading run of
    exactly 0.5 is the listing placeholder before any trade and is ignored.
    """
    pts = [(int(p["t"]), float(p["p"])) for p in hist
           if p.get("t") is not None and p.get("p") is not None]
    if not pts:
        return None
    # drop the untraded placeholder run at listing
    i = 0
    while i < len(pts) and abs(pts[i][1] - 0.5) < 1e-9:
        i += 1
    pts = pts[i:] if i < len(pts) else []
    if not pts:
        return None
    target = start_ts - lead_hours * 3600
    entry = min(pts, key=lambda tp: abs(tp[0] - target))
    if abs(entry[0] - target) > 3 * 3600:
        return None
    pre = [tp for tp in pts if tp[0] < start_ts]
    if not pre:
        return None
    mid = entry[1]
    if not (0.02 < mid < 0.98):
        return None
    closing = pre[-1][1]
    # A closing print at the rail is a resolved or nearly-resolved market:
    # the anchor was late (a start time that slipped, a match already
    # decided). Refuse it rather than book a CLV that is really the result.
    if not (POST_RESULT_RAIL < closing < 1.0 - POST_RESULT_RAIL):
        return None
    half = spread / 2.0
    return round(mid - half, 4), round(mid + half, 4), closing


def _fee(price: float, shares: float, rate: float) -> float:
    """Documented sports fee: rate x p x (1 - p) x shares (rate 0.05 taker,
    0.0 maker). The rebate makers receive is ignored — conservative."""
    return rate * price * (1.0 - price) * shares


def run_backtest(history, games: list[PMGame], predict, update, key_of,
                 lead_hours: float = 6.0, min_edge: float = 0.03,
                 model_weight: float = 0.30, slippage: float = 0.005,
                 kelly: float = 0.25, bankroll: float = 100.0,
                 max_stake: float = 8.0, fee_rate: float = 0.05,
                 maker: bool = False, maker_fee_rate: float = 0.0,
                 http=None) -> Result:
    """Generic day-batched walk-forward.

    `history` is chronological model input (GameResults or MatchResults);
    `key_of(item)` -> (date, home, away) as the PMGame side would see it;
    `predict(home, away, item)` -> P(home wins) with the model as it stands;
    `update(item)` applies the result. Predictions for a day are made before
    any of that day's updates, so nothing on a date can inform itself.
    `maker=True` prices entry at the bid instead of the ask and charges
    `maker_fee_rate` (documented: zero, plus a rebate that is ignored here);
    fills are assumed, which is optimistic, and the caller should say so.
    """
    from sportsbot.core.staking import kelly_binary

    by_key = {}
    for g in games:
        by_key[(g.date.date(), g.home, g.away)] = g
        by_key[(g.date.date(), g.away, g.home)] = g
    res = Result()
    by_day: dict = {}
    for item in history:
        by_day.setdefault(key_of(item)[0], []).append(item)

    for day in sorted(by_day):
        todays = by_day[day]
        for item in todays:
            _, h, a = key_of(item)
            mg = by_key.get((day, h, a))
            if mg is None:
                continue
            res.considered += 1
            q = price_at_lead(price_history(mg.token, http=http), mg.start_ts,
                              lead_hours)
            if q is None:
                continue
            res.priced += 1
            bid, ask, closing = q
            mid = (bid + ask) / 2.0
            p_home = predict(mg.home, mg.away, item)
            if p_home is None:
                continue
            blend = (1.0 - model_weight) * mid + model_weight * p_home
            if maker:
                yes_entry, no_entry, fb = bid, 1.0 - ask, maker_fee_rate
            else:
                yes_entry, no_entry, fb = ask, 1.0 - bid, fee_rate
            yes_edge = blend - yes_entry - _fee(yes_entry, 1.0, fb) - slippage
            no_edge = (1.0 - blend) - no_entry - _fee(no_entry, 1.0, fb) - slippage
            side, prob, entry, edge, close_px = (
                ("YES", blend, yes_entry, yes_edge, closing) if yes_edge >= no_edge
                else ("NO", 1.0 - blend, no_entry, no_edge, 1.0 - closing))
            if edge >= min_edge and 0.0 < entry < 1.0:
                frac = kelly_binary(prob, entry) * kelly
                stake = min(max_stake, bankroll * max(0.0, frac))
                if stake >= 1.0:
                    won = mg.home_won if side == "YES" else not mg.home_won
                    size = stake / entry
                    fee = _fee(entry, size, fb)
                    pnl = (size - stake - fee) if won else -(stake + fee)
                    res.bets.append(Bet(
                        date=mg.date, market=mg.slug, side=side, model_prob=prob,
                        entry=round(entry, 4), close=round(close_px, 4),
                        stake=round(stake, 2), edge=round(edge, 4), won=won,
                        pnl=round(pnl, 2)))
        for item in todays:
            update(item)
    return res


# ---------------------------------------------------------------- per-sport
def run_mlb_backtest(history, games: list[PMGame], lead_hours: float = 6.0,
                     min_edge: float = 0.03, home_advantage: float = 24.0,
                     prob_shrink: float = 0.8, maker: bool = False,
                     **kw) -> Result:
    """MLB walk-forward on Polymarket prices with the production
    BaseballModel. `history` is MLB Stats GameResults; `games` come from
    `fetch_resolved("baseball")`. Polymarket lists the visitor as
    outcomes[0], so the prediction is made for the real matchup (MLB
    Stats' home/away) and restated for whichever side PMGame.home is."""
    from sportsbot.core.types import Sport
    from sportsbot.data.mlb_data import normalize_team
    from sportsbot.engine.base import EventInput
    from sportsbot.engine.baseball import BaseballModel

    model = BaseballModel(home_advantage=home_advantage, prob_shrink=prob_shrink)

    def key_of(g):
        return (g.date.date(), normalize_team(g.home), normalize_team(g.away))

    def predict(pm_home, pm_away, g):
        p = model.predict(EventInput(
            sport=Sport.BASEBALL, home=g.home, away=g.away, start_time=g.date,
            context={"home_sp": g.home_sp, "away_sp": g.away_sp})).prob_yes
        return p if pm_home == normalize_team(g.home) else 1.0 - p

    return run_backtest(history, games, predict, model.update_result, key_of,
                        lead_hours=lead_hours, min_edge=min_edge, maker=maker, **kw)


def tennis_history_with(games: list[PMGame], history) -> list:
    """Resolved Polymarket matches folded into a MatchResult history
    (deduplicated by date and player pair) — the bootstrap population."""
    from sportsbot.data.tennis_data import MatchResult, normalize_player

    seen = {(m.date.date(), frozenset((normalize_player(m.winner),
                                        normalize_player(m.loser)))) for m in history}
    out = list(history)
    for g in games:
        key = (g.date.date(), frozenset((normalize_player(g.home),
                                          normalize_player(g.away))))
        if key in seen:
            continue
        seen.add(key)
        won, lost = (g.home, g.away) if g.home_won else (g.away, g.home)
        out.append(MatchResult(date=g.date, winner=won, loser=lost, surface="",
                               best_of=3, level="", tourney="polymarket"))
    out.sort(key=lambda m: m.date)
    return out


def run_tennis_backtest(history, games: list[PMGame], lead_hours: float = 6.0,
                        min_edge: float = 0.03, surface_weight: float = 0.5,
                        min_matches: int = 10, max_uncertainty: float = 0.20,
                        maker: bool = False, **kw) -> Result:
    """Tennis walk-forward on Polymarket prices, day-batched. `history` is
    MatchResults (see `tennis_history_with`); matches the live scanner would
    not price (uncertainty above `max_uncertainty`) are skipped."""
    from sportsbot.core.types import Sport
    from sportsbot.data.tennis_data import normalize_player
    from sportsbot.engine.base import EventInput
    from sportsbot.engine.tennis import TennisModel

    model = TennisModel(surface_weight=surface_weight, min_matches=min_matches)
    games = [PMGame(**{**g.__dict__, "home": normalize_player(g.home),
                       "away": normalize_player(g.away)}) for g in games]

    def key_of(m):
        return (m.date.date(), normalize_player(m.winner), normalize_player(m.loser))

    def predict(h, a, m):
        pred = model.predict(EventInput(sport=Sport.TENNIS, home=h, away=a, best_of=3))
        return None if pred.uncertainty > max_uncertainty else pred.prob_yes

    def update(m):
        model.update_result(m.winner, m.loser, surface="", when=m.date)

    return run_backtest(history, games, predict, update, key_of,
                        lead_hours=lead_hours, min_edge=min_edge, maker=maker, **kw)
