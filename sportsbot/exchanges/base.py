"""Venue-client contract. Both Polymarket and Kalshi clients implement this;
the bot trades through the interface only, so venues are swappable and a
paper-trading implementation can stand in for either.
"""

from __future__ import annotations

import abc
from typing import Optional

from sportsbot.core.types import (
    Exchange,
    MarketInfo,
    MarketQuote,
    Order,
    Position,
    Side,
)


class ExchangeClient(abc.ABC):
    exchange: Exchange

    # --- discovery -------------------------------------------------------
    @abc.abstractmethod
    def list_sports_markets(self, sport_tag: str) -> list[MarketInfo]:
        """Active markets for a sport (venue tag/series, e.g. 'tennis', 'MLB')."""

    @abc.abstractmethod
    def get_quote(self, market: MarketInfo) -> MarketQuote:
        """Current YES order book / top of book."""

    # --- trading ---------------------------------------------------------
    @abc.abstractmethod
    def place_order(self, order: Order) -> Order:
        """Submit; returns the order with venue id + status. Must be safe to
        retry only via a fresh client_id (no blind resubmits)."""

    @abc.abstractmethod
    def cancel_order(self, order_id: str) -> bool: ...

    @abc.abstractmethod
    def get_open_orders(self) -> list[Order]: ...

    @abc.abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abc.abstractmethod
    def get_balance(self) -> float:
        """Available spendable balance (USDC / USD)."""

    def get_resolution(self, market_id: str) -> Optional[bool]:
        """True if YES resolved as winner, False if NO, None if unresolved
        (or the venue doesn't support this query)."""
        return None

    # --- convenience -----------------------------------------------------
    def buy_price_for(self, quote: MarketQuote, side: Side) -> Optional[float]:
        """Price to buy `side` at, from a YES-side book: YES buys at the ask,
        NO buys at (1 - bid)."""
        if side == Side.YES:
            return quote.ask
        return None if quote.bid is None else round(1.0 - quote.bid, 6)
