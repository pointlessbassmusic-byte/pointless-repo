"""Result 15 on Kalshi: does a bid resting at the MLB touch earn CLV on the
venue the live maker test can actually use?

Result 15 measured it on Polymarket's main-CLOB tape, which a US account
cannot trade; Polymarket US publishes no keyless tape. Kalshi does
(`/markets/trades`, with `taker_side`), so the same construction runs here
on settled `KXMLBGAME` games:

* every pre-start taker print; a maker rests one tick under the taker's
  price on the taker's side (the most the tape says about the book);
* **upper bound** (the paper venue's rule, `core.books.print_crosses`,
  front of queue implied): filled by the first later pre-start print at
  or through the rest, within the deadline;
* **lower bound** (strict): filled only when a later print trades
  strictly THROUGH the rest, so the level was swept and queue position
  cannot matter;
* CLV = side close (last pre-start print, refused at the rail) minus the
  rest price, gross and net of the venue's maker fee
  (`kalshi_maker_fee_per_share`, the repo's deliberately non-zero default
  unless `KALSHI_MAKER_FEE_PER_CONTRACT` is set).

Start time: the EARLIER of the event ticker's time and MLB's official
scheduled first pitch. The ticker is not a reliable first pitch: the
2026-10-12 LAD@MIL ticker says 20:00 ET for a game that started at
21:00 UTC (17:00 ET), so a ticker anchor scored in-play prints as the
close and that one game carried +0.23 per fill. A game missing from the
schedule is skipped. Data only.
"""

from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sportsbot.backtest.kalshi_market import MarketGame
from sportsbot.core.books import print_crosses
from sportsbot.core.types import Side
from sportsbot.signals.sharp import cluster_bootstrap_mean

log = logging.getLogger(__name__)

TAPE_CACHE = "data/cache/kalshi_trades"
TICK = 0.01
RAIL = 0.03
DEADLINES = (("cancel 120 s", 120.0), ("cancel 30 min", 1800.0), ("rest to start", None))


@dataclass
class Print:
    ts: float
    p_yes: float      # YES price of the print
    taker_yes: bool   # the aggressor bought YES


def fetch_tape(client, ticker: str, max_pages: int = 200) -> list[Print]:
    """Every print on a ticker, oldest first, cached on disk."""
    from sportsbot.exchanges.kalshi import API_ROOT

    os.makedirs(TAPE_CACHE, exist_ok=True)
    path = os.path.join(TAPE_CACHE, f"{ticker}.json")
    rows: Optional[list] = None
    if os.path.exists(path):
        try:
            with open(path) as fh:
                rows = json.load(fh)
        except (OSError, ValueError):
            rows = None
    if rows is None:
        rows, cursor = [], None
        for _ in range(max_pages):
            params = {"ticker": ticker, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            try:
                data = client._request("GET", f"{API_ROOT}/markets/trades", params=params)
            except Exception as exc:  # noqa: BLE001 — one game is not fatal
                log.warning("tape %s failed: %s", ticker, exc)
                return []
            for r in data.get("trades") or []:
                p = r.get("yes_price_dollars")
                p = float(p) if p not in (None, "") else (
                    float(r["yes_price"]) / 100.0 if r.get("yes_price") is not None else None)
                side = str(r.get("taker_side") or "").lower()
                ts = r.get("created_time")
                if p is None or side not in ("yes", "no") or not ts:
                    continue
                rows.append([datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp(),
                             p, side == "yes"])
            cursor = data.get("cursor")
            if not cursor or not data.get("trades"):
                break
        with open(path, "w") as fh:
            json.dump(rows, fh)
    return sorted((Print(ts=r[0], p_yes=r[1], taker_yes=bool(r[2])) for r in rows),
                  key=lambda t: t.ts)


def anchor_starts(games: list[MarketGame], sched: list[dict],
                  tol_h: float = 12.0) -> list[MarketGame]:
    """Games re-anchored on min(ticker start, scheduled first pitch); a game
    the schedule does not match (both teams, within ``tol_h``) is dropped."""
    out = []
    for g in games:
        if g.start_ts is None:
            continue
        c = [x for x in sched if {x["home"], x["away"]} == {g.home, g.away}
             and abs(x["ts"] - g.start_ts) <= tol_h * 3600]
        if not c:
            continue
        s = min(c, key=lambda x: abs(x["ts"] - g.start_ts))["ts"]
        out.append(MarketGame(date=g.date, home=g.home, away=g.away,
                              home_ticker=g.home_ticker, close_ts=g.close_ts,
                              home_won=g.home_won, start_ts=int(min(g.start_ts, s))))
    return out


def close_yes(tape: list[Print], start_ts: float) -> Optional[float]:
    pre = [t for t in tape if t.ts < start_ts]
    if not pre or not (RAIL < pre[-1].p_yes < 1.0 - RAIL):
        return None
    return pre[-1].p_yes


def rest_for(t: Print) -> tuple[Side, float]:
    """Side and price (that side's frame) of a bid one tick under the taker."""
    side = Side.YES if t.taker_yes else Side.NO
    paid = t.p_yes if t.taker_yes else 1.0 - t.p_yes
    return side, round(paid - TICK, 4)


def filled(tape: list[Print], i: int, start_ts: float, side: Side, rest: float,
           ttl: Optional[float], strict: bool) -> bool:
    """A later pre-start print crosses the rest (front of queue) or, strict,
    trades through it."""
    t0 = tape[i]
    # strictly through a bid at L = at or through a bid one tick lower
    lvl = round(rest - TICK, 4) if strict else rest
    for t in tape[i + 1:]:
        if t.ts >= start_ts or (ttl is not None and t.ts - t0.ts > ttl):
            return False
        if t.ts > t0.ts and print_crosses(side, lvl, t.p_yes):
            return True
    return False


def run(games: list[MarketGame], tapes: dict[str, list[Print]], fee_fn,
        sample: int = 40_000, seed: int = 0) -> dict:
    events = []
    for g in games:
        tape = tapes.get(g.home_ticker) or []
        if g.start_ts is None or not tape:
            continue
        c = close_yes(tape, g.start_ts)
        if c is None:
            continue
        for i, t in enumerate(tape):
            if t.ts >= g.start_ts:
                break
            events.append((g, tape, i, c))
    rng = random.Random(seed)
    if len(events) > sample:
        events = rng.sample(events, sample)
    out: dict = {"prints": len(events), "games": len({e[0].home_ticker for e in events})}
    for label, ttl in DEADLINES:
        for mode in ("front", "strict"):
            vals, fees, keys = [], [], []
            for g, tape, i, c in events:
                side, rest = rest_for(tape[i])
                if not (0.0 < rest < 1.0):
                    continue
                if filled(tape, i, g.start_ts, side, rest, ttl, strict=(mode == "strict")):
                    close = c if side == Side.YES else 1.0 - c
                    vals.append(close - rest)
                    fees.append(fee_fn(rest, g.home_ticker))
                    keys.append(g.home_ticker)
            out[(label, mode)] = {
                "fill_rate": len(vals) / len(events) if events else 0.0,
                "gross": cluster_bootstrap_mean(vals, keys),
                "net": cluster_bootstrap_mean([v - f for v, f in zip(vals, fees)], keys),
                "markets": len(set(keys))}
    return out


def _ci(d: Optional[dict]) -> str:
    if not d or d.get("lo") is None:
        return "-"
    return f"{d['mean']:+.4f} [{d['lo']:+.4f}, {d['hi']:+.4f}]"


def format_report(res: dict, fee_note: str) -> str:
    lines = [f"Kalshi MLB touch-maker baseline: rest one tick under every pre-start taker "
             f"print ({res['prints']} prints, {res['games']} games); {fee_note}",
             f"{'deadline':<15}{'fills':<8}{'fill rate':>10}  {'CLV gross':<28}{'CLV net of maker fee':<28}markets"]
    for label, _ in DEADLINES:
        for mode in ("front", "strict"):
            d = res[(label, mode)]
            lines.append(f"{label:<15}{mode:<8}{d['fill_rate']:>10.1%}  {_ci(d['gross']):<28}"
                         f"{_ci(d['net']):<28}{d['markets']}")
    return "\n".join(lines)
