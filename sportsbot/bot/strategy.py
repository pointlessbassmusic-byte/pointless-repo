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

from sportsbot.core.calibration import blend_with_market
from sportsbot.core.staking import StakingConfig, decide_stake
from sportsbot.core.types import BetIntent, MarketInfo, MarketQuote, Prediction, Side

log = logging.getLogger(__name__)


@dataclass
class StrategyConfig:
    model_weight: float = 0.30
    max_spread: float = 0.03
    slippage_buffer: float = 0.005
    max_depth_fraction: float = 0.25
    post_inside_spread: bool = True
    max_uncertainty: float = 0.20     # skip predictions the model itself distrusts
    min_edge_override: dict = None    # per-sport {sport: min_edge}
    max_stake_override: dict = None   # per-sport {sport: max_stake}

    def __post_init__(self) -> None:
        self.min_edge_override = self.min_edge_override or {}
        self.max_stake_override = self.max_stake_override or {}


def walk_book(levels: list[tuple[float, float]], max_price: float,
              max_size: float) -> tuple[float, float]:
    """Average fill price and fillable size buying up to `max_price` for up
    to `max_size` shares against ascending (price, size) levels."""
    filled = 0.0
    cost = 0.0
    for price, size in levels:
        if price > max_price or filled >= max_size:
            break
        take = min(size, max_size - filled)
        filled += take
        cost += take * price
    return (cost / filled if filled else 0.0), filled


def evaluate_market(
    market: MarketInfo,
    quote: MarketQuote,
    prediction: Prediction,
    staking: StakingConfig,
    cfg: StrategyConfig,
    fee_fn: Callable[[float, float], float],
    exposure: dict,
) -> Optional[BetIntent]:
    """Return a BetIntent when one side clears every filter, else None.

    `prediction.prob_yes` = P(market YES side / outcomes[0] wins).
    `fee_fn(price, shares)` -> taker fee dollars (0 for pure maker venues).
    `exposure` = {"total": $, "by_sport": {}, "by_market": {}, "open_positions": n}.
    """
    if prediction.uncertainty > cfg.max_uncertainty:
        return None
    if quote.bid is None or quote.ask is None:
        return None
    spread = quote.ask - quote.bid
    if spread <= 0 or spread > cfg.max_spread:
        return None

    market_mid = (quote.bid + quote.ask) / 2.0
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
    for side, prob, entry, fill_cap, is_maker, levels in candidates:
        if not (0.0 < entry < 1.0) or fill_cap <= 0:
            continue
        fee_per_share = 0.0 if is_maker else fee_fn(entry, 1.0)
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
            continue

        size = min(decision.size, fill_cap)
        if not is_maker:
            # Verify the walked average fill price still clears the edge bar.
            avg_price, fillable = walk_book(levels, entry, size)
            size = min(size, fillable)
            if size <= 0 or prob - avg_price - fee_fn(avg_price, 1.0) - cfg.slippage_buffer < min_edge:
                continue
        if size * entry < staking.min_stake or size < market.min_order_size:
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
    return best
