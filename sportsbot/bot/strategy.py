"""Strategy: turn (prediction, order book) into a sized BetIntent — or nothing.

Discipline encoded here (from the staking research):
* Shrink the model toward the market: q' = w·model + (1−w)·market_mid.
* Compute edge against the size-weighted fill price from walking the book,
  never top-of-book; take at most a fraction of visible depth.
* Subtract venue taker fees and a slippage buffer before thresholding.
* Prefer maker placement one tick inside the spread; skip wide books.
* Quarter-Kelly sizing under caps happens in core.staking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from sportsbot.core.books import walk_book
from sportsbot.core.calibration import blend_with_market
from sportsbot.core.staking import StakingConfig, decide_stake
from sportsbot.core.types import BetIntent, MarketInfo, MarketQuote, Prediction, Side

__all__ = ["StrategyConfig", "evaluate_market", "walk_book"]

log = logging.getLogger(__name__)


@dataclass
class StrategyConfig:
    model_weight: float = 0.30
    max_spread: float = 0.03
    slippage_buffer: float = 0.005
    max_depth_fraction: float = 0.25
    post_inside_spread: bool = True
    max_uncertainty: float = 0.20     # skip predictions the model itself distrusts
    # Entry-price band (carried over from the tuned dry-run configs): skip
    # markets whose mid sits outside it — extreme longshots/favorites carry
    # the worst bias and the thinnest books.
    min_entry_price: float = 0.15
    max_entry_price: float = 0.85
    min_edge_override: dict = None    # per-sport {sport: min_edge}
    max_stake_override: dict = None   # per-sport {sport: max_stake}

    def __post_init__(self) -> None:
        self.min_edge_override = self.min_edge_override or {}
        self.max_stake_override = self.max_stake_override or {}


def evaluate_market(
    market: MarketInfo,
    quote: MarketQuote,
    prediction: Prediction,
    staking: StakingConfig,
    cfg: StrategyConfig,
    fee_fn: Callable[[float, float], float],
    exposure: dict,
) -> Optional[BetIntent]:
    """Return a BetIntent when one side clears every filter, else None."""
    return evaluate_market_verbose(market, quote, prediction, staking, cfg,
                                   fee_fn, exposure)[0]


def evaluate_market_verbose(
    market: MarketInfo,
    quote: MarketQuote,
    prediction: Prediction,
    staking: StakingConfig,
    cfg: StrategyConfig,
    fee_fn: Callable[[float, float], float],
    exposure: dict,
    maker_fee_fn: Optional[Callable[[float, str], float]] = None,
) -> tuple[Optional[BetIntent], str]:
    """Same decision as `evaluate_market`, plus the reason it came out that way.

    On a near-efficient slate almost every decision is a pass, so a decision
    feed that only shows bets shows nothing. The second element is that
    missing half: which filter rejected the market, with the numbers.

    `prediction.prob_yes` = P(market YES side / outcomes[0] wins).
    `maker_fee_fn(price, market_id)` -> cost of a RESTING contract. It
    defaults to zero for venues whose makers pay nothing, but Kalshi's sports
    series charge makers, and this strategy prefers maker entries — so a
    default of zero there would under-cost its own favourite path.

    `fee_fn(price, shares, market_id=None)` -> taker fee dollars (0 for pure
    maker venues); the market id lets venues with per-series fees price it.
    `exposure` = {"total": $, "by_sport": {}, "by_market": {}, "open_positions": n}.
    """
    if maker_fee_fn is None:
        def maker_fee_fn(price, market_id=""):   # noqa: ARG001 — venue default
            return 0.0

    if prediction.uncertainty > cfg.max_uncertainty:
        return None, (f"model uncertainty {prediction.uncertainty:.3f} over "
                      f"{cfg.max_uncertainty:.2f} — not confident enough to price")
    if quote.bid is None or quote.ask is None:
        return None, "no two-sided book"
    spread = quote.ask - quote.bid
    if spread <= 0:
        return None, "crossed or empty book"
    if spread > cfg.max_spread:
        return None, (f"spread {spread:.3f} wider than the {cfg.max_spread:.3f} "
                      f"limit — fills would give back the edge")

    market_mid = (quote.bid + quote.ask) / 2.0
    if not (cfg.min_entry_price <= market_mid <= cfg.max_entry_price):
        return None, (f"mid {market_mid:.3f} outside the tradable band "
                      f"[{cfg.min_entry_price:.2f}, {cfg.max_entry_price:.2f}]")
    q = blend_with_market(prediction.prob_yes, market_mid, cfg.model_weight)

    sport_key = market.sport.value if market.sport else "unknown"
    min_edge = cfg.min_edge_override.get(sport_key, staking.min_edge)
    max_stake = cfg.max_stake_override.get(sport_key, staking.max_stake_per_market)

    tick = market.tick_size or 0.01

    candidates = []
    # --- YES side: buy outcome A -----------------------------------------
    yes_levels = [(lvl.price, lvl.size) for lvl in quote.asks]
    depth_yes = sum(s for _, s in yes_levels[:5]) * cfg.max_depth_fraction
    if cfg.post_inside_spread and spread > tick:
        entry_yes = round(quote.bid + tick, 4)   # maker: improve best bid
        fill_cap_yes = depth_yes                  # sizing bound only
        maker_yes = True
    else:
        entry_yes, fill_cap_yes = quote.ask, depth_yes
        maker_yes = False
    candidates.append((Side.YES, q, entry_yes, fill_cap_yes, maker_yes, yes_levels))

    # --- NO side: buy outcome B ------------------------------------------
    no_levels = [(round(1.0 - lvl.price, 6), lvl.size) for lvl in quote.bids]  # ascending
    depth_no = sum(s for _, s in no_levels[:5]) * cfg.max_depth_fraction
    if cfg.post_inside_spread and spread > tick:
        entry_no = round((1.0 - quote.ask) + tick, 4)
        fill_cap_no = depth_no
        maker_no = True
    else:
        entry_no, fill_cap_no = round(1.0 - quote.bid, 4), depth_no
        maker_no = False
    candidates.append((Side.NO, 1.0 - q, entry_no, fill_cap_no, maker_no, no_levels))

    best: Optional[BetIntent] = None
    near: tuple[float, str] | None = None   # closest miss, for the decision feed

    def miss(edge: float, why: str) -> None:
        nonlocal near
        if near is None or edge > near[0]:
            near = (edge, why)

    for side, prob, entry, fill_cap, is_maker, levels in candidates:
        if not (0.0 < entry < 1.0) or fill_cap <= 0:
            miss(-1.0, "no depth on that side")
            continue
        fee_per_share = (maker_fee_fn(entry, market.market_id) if is_maker
                         else fee_fn(entry, 1.0, market.market_id))
        eff_edge = prob - entry - fee_per_share - cfg.slippage_buffer

        local = StakingConfig(**{**staking.__dict__,
                                 "min_edge": min_edge,
                                 "max_stake_per_market": max_stake})
        decision = decide_stake(
            prob=prob,
            price=entry + fee_per_share + cfg.slippage_buffer,
            cfg=local,
            side=side,
            current_market_exposure=exposure.get("by_market", {}).get(market.market_id, 0.0),
            current_sport_exposure=exposure.get("by_sport", {}).get(sport_key, 0.0),
            current_total_exposure=exposure.get("total", 0.0),
            open_positions=exposure.get("open_positions", 0),
        )
        if not decision.approved:
            miss(eff_edge, (f"{side.value} edge {eff_edge:+.4f} vs the "
                            f"{min_edge:.3f} bar: "
                            + "; ".join(decision.reasons or ["no stake"])))
            continue

        size = min(decision.size, fill_cap)
        if not is_maker:
            # Verify the walked average fill price still clears the edge bar.
            avg_price, fillable = walk_book(levels, entry, size)
            size = min(size, fillable)
            walked_fee = fee_fn(avg_price, 1.0, market.market_id)
            if size <= 0 or prob - avg_price - walked_fee - cfg.slippage_buffer < min_edge:
                miss(eff_edge, f"{side.value} edge gone after walking the book")
                continue
        if size * entry < staking.min_stake or size < market.min_order_size:
            miss(eff_edge, (f"{side.value} size {size:.1f} @ {entry:.3f} under the "
                            f"${staking.min_stake:.0f} minimum stake"))
            continue

        intent = BetIntent(
            market=market,
            side=side,
            prob=prob,
            price=entry,
            size=round(size, 2),
            edge=round(eff_edge, 4),
            kelly_fraction=decision.kelly_fraction,
            reason=(f"{prediction.model} p={prediction.prob_yes:.3f} "
                    f"blend={q:.3f} mid={market_mid:.3f} "
                    f"{'maker' if is_maker else 'taker'}"),
        )
        if best is None or intent.edge > best.edge:
            best = intent
    if best is not None:
        return best, best.reason
    return None, (near[1] if near else "no tradable side")
