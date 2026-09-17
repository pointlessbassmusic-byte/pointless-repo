"""Position management: reacting to open-position P&L the only way that
preserves expectancy — by re-pricing the *edge of continuing to hold*, never
by reference to the entry price, the loss so far, or a desire to win it back.

What reacting to losses looks like here (all of it risk-REDUCING):

* **Edge-reversal exit** — each cycle every open position is re-priced with
  the same blend used at entry (model shrunk toward the current market). If
  the expected value of holding falls more than `exit_edge` below what the
  book pays to exit now, the position is closed. A big adverse price move
  drags the blend down with it, so genuinely-losing positions exit — but
  because the *edge* died, not because the P&L is red.
* **Hard stop** — if the sellable value of a position drops below
  `stop_fraction` of its cost basis, close it regardless of what the model
  thinks: the model may simply be wrong, and the stop caps the tail.
* **Drawdown-scaled Kelly** (`scaled_kelly`) — stakes shrink toward a floor
  as realized drawdown approaches the kill-switch level. Anti-martingale:
  losing streaks bet SMALLER, never bigger.
* **Adaptive per-sport tightening** (`adaptive_overrides`) — a sport whose
  rolling closing-line value goes negative gets a higher min-edge bar and a
  smaller stake cap until its CLV recovers. Tighten-only: overrides are
  never loosened below the configured base.

What is deliberately NOT here: martingale / doubling down / "flipping the
position to claw back losses". After an exit, the opposite side is entered
only if it independently clears the same bar as any fresh bet (min edge,
caps, risk checks) on the next scan — a prior loss never lowers that bar or
raises the stake. Chasing losses raises risk of ruin without changing
expected value; nothing in this module or the runner may implement it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from sportsbot.core.books import sell_levels, walk_sell
from sportsbot.core.calibration import blend_with_market
from sportsbot.core.types import MarketQuote, Side

log = logging.getLogger(__name__)

__all__ = ["PositionConfig", "ExitDecision", "aggregate_open_bets",
           "evaluate_exit", "scaled_kelly", "adaptive_overrides",
           "category_report"]


@dataclass
class PositionConfig:
    manage: bool = True
    exit_edge: float = -0.05       # close when hold-EV < exit value + this
    stop_fraction: float = 0.5     # close when sellable value < this × cost
    min_hold_minutes: float = 30.0  # no churn right after entry
    slippage_buffer: float = 0.005
    min_exit_price: float = 0.02   # never dump into a collapsed book below this


@dataclass
class ExitDecision:
    close: bool
    reason: str
    exit_price: float = 0.0        # avg walked exit price (side frame)
    sellable: float = 0.0


def aggregate_open_bets(open_bets: list[dict]) -> dict[tuple[str, str], dict]:
    """Group open bet rows into (market_id, side) aggregates matching the
    exchange's position keying: total size/stake, stake-weighted model prob,
    newest entry timestamp (for the hold timer), and the row ids."""
    out: dict[tuple[str, str], dict] = {}
    for b in open_bets:
        key = (b["market_id"], b["side"])
        agg = out.setdefault(key, {
            "market_id": b["market_id"], "side": b["side"], "size": 0.0,
            "stake": 0.0, "model_prob_w": 0.0, "last_ts": b["ts"], "bets": [],
        })
        stake = b.get("stake") or 0.0
        agg["size"] += b.get("size") or 0.0
        agg["stake"] += stake
        agg["model_prob_w"] += (b.get("model_prob") or 0.5) * stake
        agg["last_ts"] = max(agg["last_ts"], b["ts"])
        agg["bets"].append((b["id"], stake))
    for agg in out.values():
        agg["model_prob"] = (agg["model_prob_w"] / agg["stake"]
                             if agg["stake"] > 0 else 0.5)
    return out


def evaluate_exit(
    agg: dict,
    quote: MarketQuote,
    cfg: PositionConfig,
    model_weight: float = 0.30,
    fee_fn: Optional[Callable[[float, float], float]] = None,
    now: Optional[datetime] = None,
) -> ExitDecision:
    """Should this (market, side) aggregate be closed at the current book?

    `agg` comes from `aggregate_open_bets`; `agg['model_prob']` is the stale
    entry-time model prob for the side held — the blend leans on the CURRENT
    market mid (weight 1 - model_weight), so the live market dominates.

    Note the edge rule is two-sided: because the model term is stale, a
    FAVORABLE move beyond ~(|exit_edge| + exit costs) / model_weight
    (≈ 22 points at defaults) also trips it — an implicit take-profit that
    banks ~0.95 on the dollar instead of waiting for settlement. That is
    deliberate: it frees capital and sheds late-breaking event risk for a
    small spread cost (see backtest/exit_replay.py for the measurement).
    """
    hold = ExitDecision(False, "hold")
    if not cfg.manage or agg["size"] <= 0:
        return hold
    if quote.bid is None or quote.ask is None:
        return ExitDecision(False, "no quote")

    now = now or datetime.now(timezone.utc)
    try:
        entered = datetime.fromisoformat(agg["last_ts"])
        if entered.tzinfo is None:
            entered = entered.replace(tzinfo=timezone.utc)
        if (now - entered).total_seconds() < cfg.min_hold_minutes * 60.0:
            return ExitDecision(False, "min hold")
    except (ValueError, TypeError):
        pass  # unparseable timestamp: no hold protection, evaluate normally

    side = Side(agg["side"])
    mid = (quote.bid + quote.ask) / 2.0
    market_side_prob = mid if side == Side.YES else 1.0 - mid
    q = blend_with_market(agg["model_prob"], market_side_prob, model_weight)

    exit_avg, sellable = walk_sell(sell_levels(quote, side),
                                   cfg.min_exit_price, agg["size"])
    sellable = min(sellable, agg["size"])
    if sellable <= 0 or exit_avg <= 0:
        return ExitDecision(False, "no exit liquidity")

    fee_per_share = fee_fn(exit_avg, 1.0) if fee_fn else 0.0
    exit_net = exit_avg - fee_per_share - cfg.slippage_buffer

    # Hard stop: sellable value vs cost basis — the loss-reactive control,
    # and it only ever CUTS risk.
    exit_value = exit_net * sellable
    if agg["stake"] > 0 and exit_value < cfg.stop_fraction * agg["stake"]:
        return ExitDecision(True, f"hard stop: value {exit_value:.2f} < "
                                  f"{cfg.stop_fraction:.0%} of {agg['stake']:.2f}",
                            exit_price=exit_avg, sellable=sellable)

    # Edge reversal: expected value of holding vs cashing out now.
    hold_edge = q - exit_net
    if hold_edge < cfg.exit_edge:
        return ExitDecision(True, f"edge reversed: hold EV {q:.3f} vs exit "
                                  f"{exit_net:.3f} ({hold_edge:+.3f} < "
                                  f"{cfg.exit_edge:+.3f})",
                            exit_price=exit_avg, sellable=sellable)
    return hold


def scaled_kelly(base_multiplier: float, drawdown: float, max_drawdown: float,
                 floor_fraction: float = 0.25) -> float:
    """Anti-martingale stake scaling: the Kelly multiplier shrinks linearly
    toward `floor_fraction` of its base as realized drawdown approaches the
    kill-switch level. Never exceeds the base; never negative."""
    if max_drawdown <= 0:
        return base_multiplier
    frac = max(0.0, min(1.0, drawdown / max_drawdown))
    return base_multiplier * max(floor_fraction, 1.0 - frac)


def adaptive_overrides(
    settled: list[dict],
    sports_base_edge: dict[str, float],
    default_min_edge: float,
    base_max_stake: dict[str, float],
    default_max_stake: float,
    window: int = 50,
    min_bets: int = 30,
    tighten_edge: float = 0.02,
    stake_cut: float = 0.5,
) -> tuple[dict[str, float], dict[str, float], list[str]]:
    """Per-sport tighten-only overrides from rolling realized CLV.

    For each sport with >= `min_bets` CLV-bearing bets in its last `window`,
    a NEGATIVE mean CLV raises that sport's min edge by `tighten_edge` and
    cuts its stake cap by `stake_cut`. A positive CLV restores the configured
    base — never anything looser than it. Returns (min_edge_overrides,
    max_stake_overrides, tightened_sport_names)."""
    by_sport: dict[str, list[float]] = {}
    for r in settled:  # newest first from the store
        clv_ok = (r.get("closing_price") is not None
                  and r.get("entry_price") is not None)
        if not clv_ok or not r.get("sport"):
            continue
        rows = by_sport.setdefault(r["sport"], [])
        if len(rows) < window:
            rows.append(r["closing_price"] - r["entry_price"])

    edge_over = dict(sports_base_edge)
    stake_over = dict(base_max_stake)
    tightened: list[str] = []
    for sport, clvs in by_sport.items():
        if len(clvs) < min_bets:
            continue
        if sum(clvs) / len(clvs) < 0.0:
            base_edge = sports_base_edge.get(sport, default_min_edge)
            base_stake = base_max_stake.get(sport, default_max_stake)
            edge_over[sport] = base_edge + tighten_edge
            stake_over[sport] = base_stake * stake_cut
            tightened.append(sport)
    return edge_over, stake_over, tightened


def category_report(
    settled: list[dict],
    adaptive: dict,
    base_min_edge: float,
    base_max_stake: float,
    sports_base_edge: dict[str, float],
    sports_base_stake: dict[str, float],
    base_kelly: float,
    max_drawdown: float,
) -> dict:
    """Per-category performance + the adaptive layer's current state, for
    `sportsbot status` and the dashboard's ops panel. Pure read: computes
    exactly what the runner's `_cycle_configs` would apply next cycle.

    Returns {"by_sport": {sport: {n, pnl, mean_clv, hit_rate, tightened,
    min_edge, max_stake}}, "current_drawdown", "effective_kelly",
    "tightened": [...]}.
    """
    stats: dict[str, dict] = {}
    for r in settled:
        s = r.get("sport") or "unknown"
        d = stats.setdefault(s, {"n": 0, "pnl": 0.0, "clvs": [],
                                 "wins": 0, "n_outcome": 0})
        d["n"] += 1
        d["pnl"] += r.get("pnl") or 0.0
        if (r.get("closing_price") is not None
                and r.get("entry_price") is not None):
            d["clvs"].append(r["closing_price"] - r["entry_price"])
        if r.get("outcome") is not None:
            d["n_outcome"] += 1
            d["wins"] += r["outcome"]

    edge_over, stake_over, tightened = adaptive_overrides(
        settled, sports_base_edge, base_min_edge,
        sports_base_stake, base_max_stake,
        window=int(adaptive.get("clv_window", 50)),
        min_bets=int(adaptive.get("clv_min_bets", 30)),
        tighten_edge=float(adaptive.get("tighten_edge", 0.02)),
        stake_cut=float(adaptive.get("stake_cut", 0.5)),
    )

    cum = peak = 0.0
    for r in reversed(settled):  # oldest first
        cum += r.get("pnl") or 0.0
        peak = max(peak, cum)
    current_dd = peak - cum
    eff_kelly = base_kelly
    if bool(adaptive.get("drawdown_stake_scaling", True)):
        eff_kelly = scaled_kelly(base_kelly, current_dd, max_drawdown,
                                 float(adaptive.get("stake_floor", 0.25)))

    by_sport = {}
    for sport, d in sorted(stats.items()):
        by_sport[sport] = {
            "n": d["n"],
            "pnl": round(d["pnl"], 2),
            "mean_clv": (round(sum(d["clvs"]) / len(d["clvs"]), 4)
                         if d["clvs"] else None),
            "hit_rate": (round(d["wins"] / d["n_outcome"], 4)
                         if d["n_outcome"] else None),
            "tightened": sport in tightened,
            "min_edge": round(edge_over.get(sport, base_min_edge), 4),
            "max_stake": round(stake_over.get(sport, base_max_stake), 2),
        }
    return {"by_sport": by_sport, "current_drawdown": round(current_dd, 2),
            "effective_kelly": round(eff_kelly, 4), "tightened": tightened}
