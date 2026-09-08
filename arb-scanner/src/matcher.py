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
    "market", "prediction", "bet", "odds", "vs", "v", "yes", "no",
    # direction words ('above'/'below'/...) are deliberately NOT stopwords:
    # dropping them made polarity-inverted markets tokenize identically and
    # pair up, turning a double directional bet into a phantom "arb"
}

_UP_WORDS = {"above", "over", "exceed", "exceeds", "higher", "more"}
_DOWN_WORDS = {"below", "under", "lower", "less", "fewer"}


def _polarity(toks: frozenset[str]) -> int:
    """+1 above-ish, -1 below-ish, 0 neutral/both."""
    up, down = bool(toks & _UP_WORDS), bool(toks & _DOWN_WORDS)
    if up == down:
        return 0
    return 1 if up else -1

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
        p_pol = _polarity(pt)
        best, best_sim = None, min_similarity
        for k, kt in k_tokens:
            if p.close_time and k.close_time and abs(p.close_time - k.close_time) > slack:
                continue
            # opposite-direction questions are the same topic but inverted
            # outcomes — "buying both sides" would be one big directional bet
            if p_pol * _polarity(kt) == -1:
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
