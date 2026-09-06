"""Match markets representing the same prediction across platforms.

Token-set (Jaccard) similarity over normalized question text, gated by
close-time compatibility. Concepts adapted from ImMike/polymarket-arbitrage's
MarketMatcher; implementation is original.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from .feeds import BinaryMarket

STOPWORDS = {
    "will", "the", "a", "an", "be", "to", "in", "on", "by", "at", "of", "for",
    "what", "who", "which", "when", "is", "are", "was", "were", "or", "and",
    "market", "prediction", "bet", "odds", "vs", "v",
    "yes", "no", "above", "below", "over", "under",  # keep numbers, drop direction fillers
}

# common aliases so "Bitcoin" matches "BTC" etc.
ALIASES = {
    "btc": "bitcoin", "eth": "ethereum", "sol": "solana",
    "potus": "president", "gop": "republican", "dems": "democratic",
    "nyc": "new york", "la": "los angeles", "sf": "san francisco",
}


def tokens(text: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower())
    return frozenset(ALIASES.get(w, w) for w in words if w not in STOPWORDS)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class MarketPair:
    poly: BinaryMarket
    kalshi: BinaryMarket
    similarity: float


def find_pairs(
    poly_markets: list[BinaryMarket],
    kalshi_markets: list[BinaryMarket],
    min_similarity: float = 0.5,
    close_time_slack_hours: float = 26.0,
) -> list[MarketPair]:
    """Best Kalshi match per Polymarket market above the similarity threshold.

    Close times must agree within the slack window when both are known — the
    same real-world question resolves at (nearly) the same time everywhere.
    """
    slack = timedelta(hours=close_time_slack_hours)
    k_tokens = [(m, tokens(m.question)) for m in kalshi_markets]

    pairs: list[MarketPair] = []
    for p in poly_markets:
        pt = tokens(p.question)
        if not pt:
            continue
        best, best_sim = None, min_similarity
        for k, kt in k_tokens:
            if p.close_time and k.close_time and abs(p.close_time - k.close_time) > slack:
                continue
            sim = jaccard(pt, kt)
            if sim > best_sim:
                best, best_sim = k, sim
        if best is not None:
            pairs.append(MarketPair(poly=p, kalshi=best, similarity=best_sim))

    # one Polymarket market per Kalshi market: keep the highest-similarity claim
    pairs.sort(key=lambda x: x.similarity, reverse=True)
    used: set[str] = set()
    unique = []
    for pr in pairs:
        if pr.kalshi.market_id in used:
            continue
        used.add(pr.kalshi.market_id)
        unique.append(pr)
    return unique
