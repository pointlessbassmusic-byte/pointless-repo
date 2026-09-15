"""Arbitrage detection — consolidated from the polymarket-arbitrage fork's
strategy (ImMike/polymarket-arbitrage), rebuilt on the 2026 venue clients.

Two model-free opportunity classes:

1. **Bundle arb** (single venue): YES ask + NO ask < 1 − fees. Buying both
   sides locks a risk-free payout of 1 per pair at expiry.
2. **Cross-venue arb** (Polymarket vs Kalshi): the same match priced on both
   venues. Buy YES on one venue and NO (i.e. YES of the opponent) on the
   other when the combined cost < 1 − fees − buffer. Requires a confident
   entity match on BOTH participants; anything ambiguous is skipped.

These are rare and small, but they are the highest-certainty edge the system
can find, and cost nothing to scan for during normal cycles.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from sportsbot.bot.matching import similarity
from sportsbot.core.types import MarketInfo, MarketQuote

log = logging.getLogger(__name__)


@dataclass
class ArbOpportunity:
    kind: str                      # "bundle" | "cross_venue"
    description: str
    legs: list[dict]               # [{exchange, market_id, side, price, size_cap}]
    cost_per_pair: float           # combined cost of a 1/1 payout pair, incl. fees
    profit_per_pair: float         # 1 - cost_per_pair
    max_pairs: float               # liquidity-limited size

    @property
    def roi(self) -> float:
        return self.profit_per_pair / self.cost_per_pair if self.cost_per_pair > 0 else 0.0


def find_bundle_arb(
    market: MarketInfo,
    quote: MarketQuote,
    fee_fn: Callable[[float, float], float],
    min_profit: float = 0.005,
) -> Optional[ArbOpportunity]:
    """YES ask + NO ask < 1 − fees on one book.

    NO ask is derived from the YES bid side: buying NO at (1 − yes_bid).
    """
    if quote.ask is None or quote.bid is None:
        return None
    yes_ask = quote.ask
    no_ask = round(1.0 - quote.bid, 6)
    fees = fee_fn(yes_ask, 1.0) + fee_fn(no_ask, 1.0)
    cost = yes_ask + no_ask + fees
    profit = 1.0 - cost
    if profit < min_profit:
        return None
    ask_depth = quote.asks[0].size if quote.asks else 0.0
    bid_depth = quote.bids[0].size if quote.bids else 0.0
    max_pairs = min(ask_depth, bid_depth)
    if max_pairs <= 0:
        return None
    return ArbOpportunity(
        kind="bundle",
        description=f"{market.slug or market.market_id}: YES {yes_ask:.3f} + NO {no_ask:.3f}",
        legs=[
            {"exchange": market.exchange.value, "market_id": market.market_id,
             "side": "yes", "price": yes_ask, "size_cap": max_pairs},
            {"exchange": market.exchange.value, "market_id": market.market_id,
             "side": "no", "price": no_ask, "size_cap": max_pairs},
        ],
        cost_per_pair=round(cost, 4),
        profit_per_pair=round(profit, 4),
        max_pairs=max_pairs,
    )


def match_cross_venue(
    pm_markets: list[MarketInfo],
    kalshi_markets: list[MarketInfo],
    min_similarity: float = 0.88,
) -> list[tuple[MarketInfo, MarketInfo, bool]]:
    """Pair Polymarket and Kalshi markets covering the same match.

    Returns (pm, kalshi, aligned) where aligned=True means pm YES side and
    kalshi YES contract refer to the same participant.
    """
    pairs: list[tuple[MarketInfo, MarketInfo, bool]] = []
    for pm in pm_markets:
        if not pm.home or not pm.away:
            continue
        for km in kalshi_markets:
            if pm.sport != km.sport or not km.home:
                continue
            # Kalshi: home = yes_sub_title (participant YES pays on).
            same = similarity(pm.home, km.home)
            flipped = similarity(pm.away, km.home)
            # The other participant must also appear somewhere in the title.
            if same >= min_similarity and flipped < 0.5:
                other_ok = similarity(pm.away, km.question) > 0.4 or km.away is None
                if other_ok:
                    pairs.append((pm, km, True))
                    break
            elif flipped >= min_similarity and same < 0.5:
                other_ok = similarity(pm.home, km.question) > 0.4 or km.away is None
                if other_ok:
                    pairs.append((pm, km, False))
                    break
    return pairs


def find_cross_venue_arb(
    pm: MarketInfo,
    pm_quote: MarketQuote,
    km: MarketInfo,
    km_quote: MarketQuote,
    aligned: bool,
    pm_fee: Callable[[float, float], float],
    k_fee: Callable[[float, float], float],
    min_profit: float = 0.01,
) -> Optional[ArbOpportunity]:
    """Best of the two pair constructions across venues.

    aligned=True: pm-YES == kalshi-YES. Pair A: buy pm YES + kalshi NO.
    Pair B: buy pm NO + kalshi YES. (Flipped when aligned=False.)
    """
    if pm_quote.ask is None or pm_quote.bid is None:
        return None
    if km_quote.ask is None or km_quote.bid is None:
        return None

    pm_yes_ask, pm_no_ask = pm_quote.ask, round(1.0 - pm_quote.bid, 6)
    k_yes_ask, k_no_ask = km_quote.ask, round(1.0 - km_quote.bid, 6)
    if not aligned:
        k_yes_ask, k_no_ask = k_no_ask, k_yes_ask

    pm_ask_depth = pm_quote.asks[0].size if pm_quote.asks else 0.0
    pm_bid_depth = pm_quote.bids[0].size if pm_quote.bids else 0.0
    k_ask_depth = km_quote.asks[0].size if km_quote.asks else 0.0
    k_bid_depth = km_quote.bids[0].size if km_quote.bids else 0.0
    if not aligned:
        k_ask_depth, k_bid_depth = k_bid_depth, k_ask_depth

    combos = [
        # (pm side, pm price, pm depth, kalshi side, kalshi price, kalshi depth)
        ("yes", pm_yes_ask, pm_ask_depth, "no", k_no_ask, k_bid_depth),
        ("no", pm_no_ask, pm_bid_depth, "yes", k_yes_ask, k_ask_depth),
    ]
    best: Optional[ArbOpportunity] = None
    for pm_side, pm_price, pm_depth, k_side, k_price, k_depth in combos:
        cost = pm_price + k_price + pm_fee(pm_price, 1.0) + k_fee(k_price, 1.0)
        profit = 1.0 - cost
        max_pairs = min(pm_depth, k_depth)
        if profit < min_profit or max_pairs <= 0:
            continue
        opp = ArbOpportunity(
            kind="cross_venue",
            description=(f"{pm.slug or pm.market_id} <-> {km.market_id}: "
                         f"pm {pm_side}@{pm_price:.3f} + kalshi {k_side}@{k_price:.3f}"),
            legs=[
                {"exchange": "polymarket", "market_id": pm.market_id,
                 "side": pm_side, "price": pm_price, "size_cap": max_pairs},
                {"exchange": "kalshi", "market_id": km.market_id,
                 "side": k_side if aligned else ("no" if k_side == "yes" else "yes"),
                 "price": k_price, "size_cap": max_pairs},
            ],
            cost_per_pair=round(cost, 4),
            profit_per_pair=round(profit, 4),
            max_pairs=max_pairs,
        )
        if best is None or opp.profit_per_pair > best.profit_per_pair:
            best = opp
    return best
