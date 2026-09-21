"""Order-book helpers shared by the strategy layer and the paper simulator.

All quotes in the system are YES-frame: `bids`/`asks` are for the YES token.
Buying NO is buying against the YES bids at price (1 - bid).
"""

from __future__ import annotations

from sportsbot.core.types import MarketQuote, Side


def buy_levels(quote: MarketQuote, side: Side) -> list[tuple[float, float]]:
    """Ascending (price, size) levels available to BUY `side` from a
    YES-frame quote."""
    if side == Side.YES:
        return [(lvl.price, lvl.size) for lvl in quote.asks]
    return [(round(1.0 - lvl.price, 6), lvl.size) for lvl in quote.bids]


def sell_levels(quote: MarketQuote, side: Side) -> list[tuple[float, float]]:
    """Descending (price, size) levels available to SELL `side` into, from a
    YES-frame quote: YES sells to the bids; NO sells at (1 - ask)."""
    if side == Side.YES:
        return [(lvl.price, lvl.size) for lvl in quote.bids]
    return [(round(1.0 - lvl.price, 6), lvl.size) for lvl in quote.asks]


def walk_sell(levels: list[tuple[float, float]], min_price: float,
              max_size: float) -> tuple[float, float]:
    """Average exit price and sellable size selling down to `min_price` for
    up to `max_size` shares against descending (price, size) levels."""
    sold = 0.0
    proceeds = 0.0
    for price, size in levels:
        if price < min_price or sold >= max_size:
            break
        take = min(size, max_size - sold)
        sold += take
        proceeds += take * price
    return (proceeds / sold if sold else 0.0), sold


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
