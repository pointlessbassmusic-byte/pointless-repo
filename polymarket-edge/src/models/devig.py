"""De-vig sportsbook odds into fair probabilities."""
from __future__ import annotations

from statistics import median


def implied_probs(decimal_odds: dict[str, float]) -> dict[str, float]:
    return {k: 1.0 / v for k, v in decimal_odds.items() if v > 1.0}


def devig_proportional(decimal_odds: dict[str, float]) -> dict[str, float]:
    """Proportional (multiplicative) de-vig: normalize implied probs to sum to 1."""
    probs = implied_probs(decimal_odds)
    total = sum(probs.values())
    if total <= 0:
        return {}
    return {k: p / total for k, p in probs.items()}


def consensus_probs(book_odds: list[dict[str, float]], min_books: int = 3) -> dict[str, float]:
    """Median de-vigged probability per outcome across books.

    Only outcomes quoted by >= min_books books survive. Result is re-normalized.
    """
    per_outcome: dict[str, list[float]] = {}
    for odds in book_odds:
        for name, p in devig_proportional(odds).items():
            per_outcome.setdefault(name, []).append(p)

    kept = {name: median(ps) for name, ps in per_outcome.items() if len(ps) >= min_books}
    total = sum(kept.values())
    if not kept or total <= 0:
        return {}
    return {name: p / total for name, p in kept.items()}
