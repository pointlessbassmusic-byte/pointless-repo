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
  the match often closes BEFORE, so tennis anchors on `closedTime` minus
  three hours, exactly as the Kalshi harness does.
* The base fee is 1000 bps on these moneylines (Gamma `takerBaseFee`; CLOB
  `/fee-rate` agrees): 0.10 x min(p, 1-p) per share, five points at p=0.5.
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

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
CACHE_DIR = "data/cache/polymarket_prices"
TENNIS_PRE_MATCH_MARGIN_H = 3.0
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


def fetch_resolved(sport: str, days: int = 75, max_pages: int = 40,
                   http=None) -> list[PMGame]:
    """Resolved moneyline markets for a sport, newest first from Gamma, one
    game per event (outcomes[0]'s token; outcomes[1] is its complement)."""
    import httpx

    http = http or httpx.Client(timeout=40)
    tag = SPORT_TAGS[sport]
    cutoff = time.time() - days * 86400
    out: list[PMGame] = []
    offset = 0
    for _ in range(max_pages):
        r = http.get(f"{GAMMA}/events", params={
            "tag_id": tag, "closed": "true", "order": "endDate",
            "ascending": "false", "limit": 100, "offset": offset})
        r.raise_for_status()
        events = r.json()
        if not events:
            break
        stop = False
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
                if close_ts is None:
                    continue
                if close_ts < cutoff:
                    stop = True
                    continue
                if sport == "baseball":
                    start_ts = _ts(m.get("gameStartTime"))
                    if start_ts is None:
                        continue
                else:
                    start_ts = close_ts - int(TENNIS_PRE_MATCH_MARGIN_H * 3600)
                out.append(PMGame(
                    date=datetime.fromtimestamp(start_ts, timezone.utc),
                    slug=m.get("slug") or ev.get("slug", ""),
                    home=str(outcomes[0]).strip().lower(),
                    away=str(outcomes[1]).strip().lower(),
                    token=str(tokens[0]), start_ts=start_ts, close_ts=close_ts,
                    home_won=(p0 == 1.0),
                    volume=float(m.get("volumeNum") or 0.0)))
        if stop or len(events) < 100:
            break
        offset += 100
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
    half = spread / 2.0
    return round(mid - half, 4), round(mid + half, 4), pre[-1][1]


def _fee(price: float, shares: float, base_bps: float) -> float:
    return (base_bps / 10000.0) * min(price, 1.0 - price) * shares


def run_backtest(history, games: list[PMGame], predict, update, key_of,
                 lead_hours: float = 6.0, min_edge: float = 0.03,
                 model_weight: float = 0.30, slippage: float = 0.005,
                 kelly: float = 0.25, bankroll: float = 100.0,
                 max_stake: float = 8.0, fee_bps: float = 1000.0,
                 maker: bool = False, maker_fee_bps: float = 1000.0,
                 http=None) -> Result:
    """Generic day-batched walk-forward.

    `history` is chronological model input (GameResults or MatchResults);
    `key_of(item)` -> (date, home, away) as the PMGame side would see it;
    `predict(home, away, item)` -> P(home wins) with the model as it stands;
    `update(item)` applies the result. Predictions for a day are made before
    any of that day's updates, so nothing on a date can inform itself.
    `maker=True` prices entry at the bid instead of the ask and charges
    `maker_fee_bps`; fills are assumed, which is optimistic, and the caller
    should say so.
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
                yes_entry, no_entry, fb = bid, 1.0 - ask, maker_fee_bps
            else:
                yes_entry, no_entry, fb = ask, 1.0 - bid, fee_bps
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
