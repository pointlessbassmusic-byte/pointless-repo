"""Odds and probability conversions for binary prediction markets.

All prices are probabilities in [0, 1] unless a function name says otherwise.
"""

from __future__ import annotations


def clamp_prob(p: float, lo: float = 1e-6, hi: float = 1.0 - 1e-6) -> float:
    return max(lo, min(hi, p))


def decimal_to_prob(decimal_odds: float) -> float:
    """European decimal odds -> implied probability (vig included)."""
    if decimal_odds <= 1.0:
        raise ValueError(f"decimal odds must be > 1.0, got {decimal_odds}")
    return 1.0 / decimal_odds


def prob_to_decimal(p: float) -> float:
    return 1.0 / clamp_prob(p)


def american_to_prob(american: float) -> float:
    """American odds -> implied probability (vig included)."""
    if american == 0:
        raise ValueError("american odds cannot be 0")
    if american > 0:
        return 100.0 / (american + 100.0)
    return -american / (-american + 100.0)


def prob_to_american(p: float) -> float:
    p = clamp_prob(p)
    if p >= 0.5:
        return -100.0 * p / (1.0 - p)
    return 100.0 * (1.0 - p) / p


def cents_to_prob(cents: int | float) -> float:
    """Kalshi price (1..99 cents) -> probability."""
    return float(cents) / 100.0


def prob_to_cents(p: float) -> int:
    """Probability -> Kalshi integer cents, clamped to the tradable 1..99 range."""
    return int(max(1, min(99, round(p * 100))))


def remove_vig_two_way(p_a: float, p_b: float) -> tuple[float, float]:
    """Proportional (multiplicative) vig removal for a two-outcome market.

    Given implied probabilities that sum to > 1 (overround), rescale so they
    sum to exactly 1. Standard baseline; adequate for the tight two-way
    markets we trade.
    """
    total = p_a + p_b
    if total <= 0:
        raise ValueError("implied probabilities must be positive")
    return p_a / total, p_b / total


def overround(p_a: float, p_b: float) -> float:
    """Book margin: sum of implied probs minus 1 (0 = fair)."""
    return p_a + p_b - 1.0


def binary_fair_from_book(yes_bid: float | None, yes_ask: float | None) -> float | None:
    """Best available market-implied fair probability from a YES order book.

    Uses the bid/ask midpoint; the mid of a binary book already sums to 1
    across YES/NO so no de-vig step is needed.
    """
    if yes_bid is not None and yes_ask is not None:
        return (yes_bid + yes_ask) / 2.0
    return yes_bid if yes_bid is not None else yes_ask


def expected_value_per_share(prob: float, price: float) -> float:
    """EV of buying one share at `price` when true prob is `prob` (payout 1)."""
    return prob - price


def roi(prob: float, price: float) -> float:
    """Expected return on cost for one share."""
    if price <= 0:
        raise ValueError("price must be positive")
    return (prob - price) / price
