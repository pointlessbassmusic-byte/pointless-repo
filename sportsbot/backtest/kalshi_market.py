"""Walk-forward backtest against REAL Kalshi prices and outcomes.

`backtest/engine.py` scores the model against outcomes: it answers "is the
model calibrated". That is not the profitability question. A model can be
well calibrated and still lose money to the price, which is exactly what a
near-efficient market does to one.

This scores the model against the PRICE it would actually have paid:

* ratings are walk-forward — each game is predicted with the model state
  built only from games that finished before it, then the model is updated,
  which is precisely how the live loop runs;
* the entry price is a real recorded quote at a chosen lead before close,
  from Kalshi's hourly candlesticks, not a mid reconstructed after the fact;
* fees come from the same `kalshi_taker_fee` the executor charges;
* settlement is the market's own result.

The headline number is CLV. PnL over a few hundred bets is mostly variance;
beating the closing line is the thing that persists.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

CACHE_DIR = "data/cache/kalshi_candles"
_EVENT_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


@dataclass
class MarketGame:
    """One settled game with the ticker whose YES pays on the home team."""
    date: datetime
    home: str
    away: str
    home_ticker: str
    close_ts: int
    home_won: bool


@dataclass
class Bet:
    date: datetime
    market: str
    side: str            # "YES" (home) or "NO" (away)
    model_prob: float    # our probability for the side taken
    entry: float
    close: float
    stake: float
    edge: float
    won: bool
    pnl: float

    @property
    def clv(self) -> float:
        """Closing-line value in probability points, signed for the side."""
        return self.close - self.entry


@dataclass
class Result:
    bets: list[Bet] = field(default_factory=list)
    considered: int = 0
    priced: int = 0

    def summary(self) -> dict:
        n = len(self.bets)
        if not n:
            return {"bets": 0, "considered": self.considered, "priced": self.priced}
        staked = sum(b.stake for b in self.bets)
        pnl = sum(b.pnl for b in self.bets)
        clvs = [b.clv for b in self.bets]
        brier = sum((b.model_prob - (1.0 if b.won else 0.0)) ** 2
                    for b in self.bets) / n
        return {
            "considered": self.considered,
            "priced": self.priced,
            "bets": n,
            "staked": round(staked, 2),
            "pnl": round(pnl, 2),
            "roi": round(pnl / staked, 4) if staked else 0.0,
            "hit_rate": round(sum(1 for b in self.bets if b.won) / n, 4),
            "mean_clv": round(sum(clvs) / n, 4),
            "clv_positive_rate": round(sum(1 for c in clvs if c > 0) / n, 4),
            "brier": round(brier, 4),
            "mean_edge": round(sum(b.edge for b in self.bets) / n, 4),
        }


def event_date(event_ticker: str) -> Optional[datetime]:
    m = _EVENT_DATE.search(event_ticker)
    if not m:
        return None
    yy, mon, dd = m.groups()
    month = _MONTHS.get(mon)
    if month is None:
        return None
    try:
        return datetime(2000 + int(yy), month, int(dd), tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch_settled_games(client, series: str = "KXMLBGAME",
                        max_pages: int = 40) -> list[MarketGame]:
    """Settled two-sided games, with the home team's ticker."""
    from sportsbot.exchanges.kalshi import (
        API_ROOT,
        KALSHI_MLB_TEAMS,
        split_mlb_event,
    )

    by_event: dict[str, list[dict]] = {}
    cursor, pages = None, 0
    while pages < max_pages:
        params: dict = {"series_ticker": series, "status": "settled", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = client._request("GET", f"{API_ROOT}/markets", params=params)
        markets = data.get("markets", [])
        for m in markets:
            ev = m.get("event_ticker")
            if ev:
                by_event.setdefault(ev, []).append(m)
        cursor = data.get("cursor")
        pages += 1
        if not cursor or not markets:
            break

    out: list[MarketGame] = []
    for ev, group in by_event.items():
        if len(group) != 2:
            continue
        codes = {m["ticker"].rsplit("-", 1)[-1] for m in group}
        split = split_mlb_event(ev, codes)
        if split is None:
            continue
        away_code, home_code = split
        home_mkt = next((m for m in group
                         if m["ticker"].rsplit("-", 1)[-1] == home_code), None)
        home_name = KALSHI_MLB_TEAMS.get(home_code)
        away_name = KALSHI_MLB_TEAMS.get(away_code)
        when = event_date(ev)
        if not home_mkt or not home_name or not away_name or when is None:
            continue
        result = str(home_mkt.get("result", "")).lower()
        if result not in ("yes", "no"):
            continue
        close = home_mkt.get("close_time")
        try:
            close_ts = int(datetime.fromisoformat(
                str(close).replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError):
            continue
        out.append(MarketGame(date=when, home=home_name, away=away_name,
                              home_ticker=home_mkt["ticker"], close_ts=close_ts,
                              home_won=(result == "yes")))
    out.sort(key=lambda g: g.date)
    return out


def quote_at_lead(client, series: str, ticker: str, close_ts: int,
                  lead_hours: float, pause: float = 0.4) -> Optional[tuple]:
    """(yes_bid, yes_ask, closing_mid) `lead_hours` before close, from the
    hourly candlesticks. Cached on disk: the history never changes, and a
    sweep of a full season is thousands of calls."""
    from sportsbot.exchanges.kalshi import API_ROOT

    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{ticker}.json")
    candles = None
    if os.path.exists(path):
        try:
            with open(path) as fh:
                candles = json.load(fh)
        except (OSError, ValueError):
            candles = None
    if candles is None:
        try:
            data = client._request(
                "GET", f"{API_ROOT}/series/{series}/markets/{ticker}/candlesticks",
                params={"start_ts": close_ts - 86400 * 2, "end_ts": close_ts,
                        "period_interval": 60})
            candles = data.get("candlesticks", [])
        except Exception as exc:  # noqa: BLE001 — one missing market is not fatal
            log.debug("candles failed for %s: %s", ticker, exc)
            return None
        try:
            with open(path, "w") as fh:
                json.dump(candles, fh)
        except OSError:
            pass
        time.sleep(pause)
    if not candles:
        return None

    def dollars(c: dict, side: str, field_: str) -> Optional[float]:
        try:
            return float(c[side][field_])
        except (KeyError, TypeError, ValueError):
            return None

    target = close_ts - lead_hours * 3600
    entry = min(candles, key=lambda c: abs(c.get("end_period_ts", 0) - target))
    if abs(entry.get("end_period_ts", 0) - target) > 3 * 3600:
        return None           # nothing quoted near the decision point
    bid = dollars(entry, "yes_bid", "close_dollars")
    ask = dollars(entry, "yes_ask", "close_dollars")
    if bid is None or ask is None or ask <= bid:
        return None

    last = candles[-1]
    cb = dollars(last, "yes_bid", "close_dollars")
    ca = dollars(last, "yes_ask", "close_dollars")
    closing = ((cb + ca) / 2.0 if cb is not None and ca is not None
               else dollars(last, "price", "close_dollars"))
    if closing is None:
        return None
    return bid, ask, closing


def _match_key(when: datetime, home: str, away: str) -> tuple:
    return (when.date(), home, away)


def run_backtest(history, games: list[MarketGame], client,
                 series: str = "KXMLBGAME",
                 lead_hours: float = 6.0,
                 min_edge: float = 0.03,
                 model_weight: float = 0.30,
                 slippage: float = 0.005,
                 kelly: float = 0.25,
                 bankroll: float = 100.0,
                 max_stake: float = 8.0,
                 home_advantage: float = 24.0,
                 prob_shrink: float = 0.8) -> Result:
    """Walk forward through `history` (MLB GameResults, chronological).

    Every game is predicted with ratings built only from earlier games, then
    used to update them. Games that have a settled Kalshi market are priced
    at `lead_hours` before close and bet if the edge clears `min_edge`.
    """
    from sportsbot.core.staking import kelly_binary
    from sportsbot.core.types import Sport
    from sportsbot.engine.base import EventInput
    from sportsbot.engine.baseball import BaseballModel
    from sportsbot.exchanges.kalshi import kalshi_taker_fee

    model = BaseballModel(home_advantage=home_advantage, prob_shrink=prob_shrink)
    by_key = {_match_key(g.date, g.home, g.away): g for g in games}
    res = Result()

    for g in sorted(history, key=lambda x: x.date):
        mg = by_key.get(_match_key(g.date, g.home, g.away))
        if mg is None:                      # also try the neighbouring day:
            for delta in (-1, 1):           # Kalshi tickers are ET-dated
                alt = g.date.replace(hour=12)
                alt = alt.fromtimestamp(alt.timestamp() + delta * 86400,
                                        tz=timezone.utc)
                mg = by_key.get(_match_key(alt, g.home, g.away))
                if mg is not None:
                    break
        if mg is not None:
            res.considered += 1
            quote = quote_at_lead(client, series, mg.home_ticker,
                                  mg.close_ts, lead_hours)
            if quote is not None:
                res.priced += 1
                bid, ask, closing = quote
                mid = (bid + ask) / 2.0
                # Predict BEFORE the update: ratings hold only earlier games.
                # Go through the real `predict` path so the backtest cannot
                # drift from what the live scanner computes.
                p_home = model.predict(EventInput(
                    sport=Sport.BASEBALL, home=g.home, away=g.away,
                    start_time=g.date,
                    context={"home_sp": g.home_sp, "away_sp": g.away_sp},
                )).prob_yes
                q = (1.0 - model_weight) * mid + model_weight * p_home

                yes_fee = kalshi_taker_fee(ask, 1.0)
                no_fee = kalshi_taker_fee(1.0 - bid, 1.0)
                yes_edge = q - ask - yes_fee - slippage
                no_edge = (1.0 - q) - (1.0 - bid) - no_fee - slippage

                side, prob, entry, edge, close_px = (
                    ("YES", q, ask, yes_edge, closing) if yes_edge >= no_edge
                    else ("NO", 1.0 - q, 1.0 - bid, no_edge, 1.0 - closing))
                if edge >= min_edge and 0.0 < entry < 1.0:
                    frac = kelly_binary(prob, entry) * kelly
                    stake = min(max_stake, bankroll * max(0.0, frac))
                    if stake >= 1.0:
                        won = mg.home_won if side == "YES" else not mg.home_won
                        size = stake / entry
                        fee = kalshi_taker_fee(entry, size)
                        pnl = (size - stake - fee) if won else -(stake + fee)
                        res.bets.append(Bet(
                            date=g.date, market=mg.home_ticker, side=side,
                            model_prob=prob, entry=round(entry, 4),
                            close=round(close_px, 4), stake=round(stake, 2),
                            edge=round(edge, 4), won=won, pnl=round(pnl, 2)))
        model.update_result(g)
    return res
