"""Odds and probability conversions for binary prediction markets.

All prices are probabilities in [0, 1] unless a function name says otherwise.
"""

from __future__ import annotations

import math


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


def shin_devig(implied: list[float], tol: float = 1e-10,
               max_iter: int = 200) -> list[float]:
    """Shin (1993) de-vigging: fair probabilities from a book's implied ones.

    Multiplicative de-vigging (`remove_vig_two_way`) spreads the overround
    evenly, which is known to leave longshots over-priced; Shin models the
    book pricing against a fraction ``z`` of insider money and takes more
    of the margin out of the longshot. Štrumbelj (IJF 2014) found Shin the
    most accurate of the standard methods against outcomes, and it is the
    de-vig the sharp-line harness grades decisions against.

    With ``pi_i`` the implied probabilities and ``B = sum(pi)`` the book
    total, the fair probabilities are

        p_i = (sqrt(z^2 + 4 (1 - z) pi_i^2 / B) - z) / (2 (1 - z))

    where ``z`` in [0, 1) is chosen so that the ``p_i`` sum to 1 (bisection:
    the sum is monotone decreasing in ``z``). A book with no overround
    returns its inputs unchanged; a book below 1 (an arb) is normalised up.
    """
    if len(implied) < 2:
        raise ValueError("Shin needs at least two outcomes")
    if any(p <= 0 for p in implied):
        raise ValueError("implied probabilities must be positive")
    total = sum(implied)
    if total <= 1.0 + 1e-12:
        return [p / total for p in implied]

    def probs(z: float) -> list[float]:
        return [(math.sqrt(z * z + 4.0 * (1.0 - z) * p * p / total) - z)
                / (2.0 * (1.0 - z)) for p in implied]

    lo, hi = 0.0, 1.0 - 1e-9
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        s = sum(probs(mid))
        if abs(s - 1.0) < tol:
            break
        if s > 1.0:
            lo = mid
        else:
            hi = mid
    out = probs((lo + hi) / 2.0)
    norm = sum(out)
    return [p / norm for p in out]
