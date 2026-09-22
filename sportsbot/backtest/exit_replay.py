"""Replay the position-management exit layer over REAL resolved-market
price paths (from polymarket-bot/v2/history_downloader.py output) to
measure its insurance value: what the stop/edge-reversal rules save on
eventually-losing positions vs what they sacrifice by whipsawing out of
eventual winners.

Honesty notes (read before quoting numbers):
* Entry is a NO-SKILL proxy: buy the token at its first tick inside the
  entry band, ≥10 min before game start when known — the production bot
  enters only with model edge, so this measures the exit layer's effect
  on unskilled positions, not trading edge.
* The rule replay uses the production formula with the stale model prob
  proxied by the entry price (blend = w·p0 + (1−w)·p_now), i.e. zero
  assumed model skill — again conservative.
* Exit fills are penalized one tick of spread plus the slippage buffer.
* The replay runs through in-play ticks; production exits depend on the
  market staying discoverable, so treat in-play savings as an upper bound.

The pre-registered grid (stops × edges, incl. None) is fixed here in code;
report every cell, never just the best one.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

ENTRY_LO, ENTRY_HI = 0.15, 0.85       # production entry-price band
MIN_HOLD_S = 30 * 60                  # production min_hold_minutes
SPREAD_PENALTY = 0.01                 # one tick to cross out
SLIPPAGE = 0.005                      # production slippage buffer
MODEL_WEIGHT = 0.30                   # production blend weight
PRE_START_CUTOFF_S = 10 * 60          # production min_minutes_before_start

STOP_GRID = (None, 0.3, 0.5, 0.7)
EDGE_GRID = (None, -0.03, -0.05, -0.08)


@dataclass
class PathSim:
    """One (token, entry) simulation: PnL per $1 staked, held vs ruled."""
    sport: str
    won: bool
    entry: float
    pnl_hold: float
    pnl_rule: float
    exited: bool
    exit_reason: str


def load_paths(data_dir: str) -> list[dict]:
    """Downloader output -> [{sport, won, game_start, path:[(ts,p),...]}]."""
    out = []
    markets_csv = os.path.join(data_dir, "markets.csv")
    with open(markets_csv) as fh:
        for r in csv.DictReader(fh):
            rp = r.get("resolved_price")
            if rp in (None, ""):
                continue
            rp = float(rp)
            if not (rp <= 0.05 or rp >= 0.95):
                continue  # unresolved / ambiguous
            ppath = os.path.join(data_dir, "prices", f"{r['token_id']}.csv")
            if not os.path.exists(ppath):
                continue
            path = []
            with open(ppath) as pfh:
                for row in csv.DictReader(pfh):
                    try:
                        path.append((float(row["ts"]), float(row["price"])))
                    except (KeyError, ValueError):
                        continue
            if len(path) < 10:
                continue
            path.sort()
            gs = None
            if r.get("game_start"):
                try:
                    gs = datetime.fromisoformat(
                        r["game_start"].replace("Z", "+00:00"))
                    if gs.tzinfo is None:
                        gs = gs.replace(tzinfo=timezone.utc)
                    gs = gs.timestamp()
                except ValueError:
                    gs = None
            out.append({"sport": r.get("sport") or "unknown",
                        "won": rp >= 0.95, "game_start": gs, "path": path})
    return out


def simulate(rec: dict, stop: Optional[float], edge: Optional[float],
             inplay_exits: bool = True) -> Optional[PathSim]:
    """Enter at the first in-band (pre-start when known) tick; replay the
    exit rule over the rest of the path. Stake normalized to $1.

    inplay_exits=False restricts exits to ticks before the pre-start
    cutoff — the fill-REALISTIC regime (in-play sports books jump
    discontinuously, so 'exit at the last printed price' overstates what a
    mid-collapse close would actually get). Records with no known
    game_start can't be classified and return None in that mode."""
    path = rec["path"]
    entry_idx = None
    for i, (ts, p) in enumerate(path):
        if not (ENTRY_LO <= p <= ENTRY_HI):
            continue
        if rec["game_start"] and ts > rec["game_start"] - PRE_START_CUTOFF_S:
            break  # in-play already; production never enters here
        entry_idx = i
        break
    if entry_idx is None or entry_idx >= len(path) - 2:
        return None
    ts0, p0 = path[entry_idx]
    size = 1.0 / p0
    pnl_hold = size * 1.0 - 1.0 if rec["won"] else -1.0

    if not inplay_exits and not rec["game_start"]:
        return None
    exited, exit_net, reason = False, 0.0, ""
    for ts, p in path[entry_idx + 1:]:
        if ts - ts0 < MIN_HOLD_S:
            continue
        if not inplay_exits and ts > rec["game_start"] - PRE_START_CUTOFF_S:
            break
        net = max(0.0, p - SPREAD_PENALTY - SLIPPAGE)
        if net <= 0.02:
            continue  # production never dumps below min_exit_price
        if stop is not None and net * size < stop * 1.0:
            exited, exit_net, reason = True, net, "stop"
            break
        if edge is not None:
            q = MODEL_WEIGHT * p0 + (1.0 - MODEL_WEIGHT) * p
            if q - net < edge:
                exited, exit_net, reason = True, net, "edge"
                break
    pnl_rule = (exit_net * size - 1.0) if exited else pnl_hold
    return PathSim(sport=rec["sport"], won=rec["won"], entry=p0,
                   pnl_hold=pnl_hold, pnl_rule=pnl_rule,
                   exited=exited, exit_reason=reason)


def _grid_cells(records: list[dict], inplay_exits: bool) -> list[dict]:
    cells = []
    for stop in STOP_GRID:
        for edge in EDGE_GRID:
            if stop is None and edge is None:
                continue  # the hold baseline is the comparison, not a cell
            sims = [s for s in (simulate(r, stop, edge,
                                         inplay_exits=inplay_exits)
                                for r in records) if s is not None]
            if not sims:
                continue
            losers = [s for s in sims if not s.won]
            winners = [s for s in sims if s.won]
            delta = [s.pnl_rule - s.pnl_hold for s in sims]
            cells.append({
                "stop": stop, "edge": edge, "n": len(sims),
                "mean_delta": round(sum(delta) / len(delta), 4),
                "exit_rate": round(sum(s.exited for s in sims) / len(sims), 4),
                "loser_delta": round(sum(s.pnl_rule - s.pnl_hold
                                         for s in losers) / len(losers), 4)
                if losers else None,
                "winner_delta": round(sum(s.pnl_rule - s.pnl_hold
                                          for s in winners) / len(winners), 4)
                if winners else None,
                "saves": sum(1 for s in losers if s.exited),
                "whipsaws": sum(1 for s in winners if s.exited),
            })
    return cells


def run_grid(records: list[dict]) -> dict:
    """Every (stop, edge) cell over every path, in both fill regimes."""
    base = [s for s in (simulate(r, None, None) for r in records)
            if s is not None]
    return {
        "n_paths": len(base),
        "base_rate_win": round(sum(s.won for s in base) / len(base), 4)
        if base else None,
        "hold_mean_pnl": round(sum(s.pnl_hold for s in base) / len(base), 4)
        if base else None,
        "by_sport": {sp: sum(1 for s in base if s.sport == sp)
                     for sp in sorted({s.sport for s in base})},
        "grid": _grid_cells(records, inplay_exits=True),
        "grid_prestart": _grid_cells(records, inplay_exits=False),
        "production": {"stop": 0.5, "edge": -0.05},
        "caveats": [
            "no-skill entry proxy: deltas are insurance bounds, not edge",
            "grid (all ticks) assumes fills at printed in-play prices — "
            "optimistic during score jumps; grid_prestart is fill-realistic "
            "but only covers records with a known game start",
        ],
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", required=True,
                    help="history_downloader output dir (markets.csv + prices/)")
    ap.add_argument("--out", default="", help="also write the report JSON here")
    args = ap.parse_args()
    records = load_paths(args.data_dir)
    report = run_grid(records)
    print(json.dumps(report, indent=1))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(report, fh, indent=1)


if __name__ == "__main__":
    main()
