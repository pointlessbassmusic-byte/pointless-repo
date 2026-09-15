"""Paper-trading venue: wraps a real client's *market data* while simulating
order fills locally. This is the default mode — identical code path to live
except `place_order` never leaves the process.

Fill model (conservative):
* A limit BUY fills immediately only up to the size available at or below
  our price on the real book (walking levels), paying each level's price.
* The remainder rests and is NOT assumed to fill (no optimistic maker fills);
  resting exposure still counts against risk limits.
* Taker fees are charged with the venue's fee model.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from sportsbot.core.books import buy_levels, walk_book
from sportsbot.core.types import (
    Exchange,
    Fill,
    MarketInfo,
    MarketQuote,
    Order,
    OrderStatus,
    Position,
    Side,
)
from sportsbot.exchanges.base import ExchangeClient

log = logging.getLogger(__name__)


class PaperExchange(ExchangeClient):
    exchange = Exchange.PAPER

    def __init__(
        self,
        data_client: Optional[ExchangeClient] = None,
        starting_balance: float = 1000.0,
        fee_fn: Optional[Callable[[float, float], float]] = None,
    ) -> None:
        self.data_client = data_client
        self.balance = starting_balance
        self.fee_fn = fee_fn or (lambda price, size: 0.0)
        self.open_orders: dict[str, Order] = {}
        self.fills: list[Fill] = []
        self.positions: dict[tuple[str, Side], Position] = {}

    # --- market data: delegate to the real venue ------------------------
    def list_sports_markets(self, sport_tag: str) -> list[MarketInfo]:
        if self.data_client is None:
            return []
        return self.data_client.list_sports_markets(sport_tag)

    def get_quote(self, market: MarketInfo) -> MarketQuote:
        if self.data_client is None:
            raise RuntimeError("paper exchange has no data client")
        return self.data_client.get_quote(market)

    def get_resolution(self, market_id: str) -> Optional[bool]:
        if self.data_client is None:
            return None
        return self.data_client.get_resolution(market_id)

    # --- simulated execution -------------------------------------------
    def place_order(self, order: Order, quote: Optional[MarketQuote] = None) -> Order:
        """`quote` must be the YES-frame book the caller just evaluated
        (Executor forwards it). Without a quote the order simply rests —
        conservatively, nothing is assumed to fill."""
        order.exchange = Exchange.PAPER
        order.order_id = f"paper-{order.client_id[:12]}"

        if quote is None:
            log.warning("paper: no quote provided for %s; order rests unfilled",
                        order.market_id)

        filled = 0.0
        cost = 0.0
        if quote is not None:
            avg, filled = walk_book(buy_levels(quote, order.side), order.price, order.size)
            cost = avg * filled

        if filled > 0:
            fee = self.fee_fn(cost / filled, filled)
            total = cost + fee
            if total > self.balance:
                order.status = OrderStatus.REJECTED
                order.raw = {"error": "insufficient paper balance"}
                return order
            self.balance -= total
            avg = cost / filled
            self.fills.append(
                Fill(order_id=order.order_id, market_id=order.market_id,
                     side=order.side, price=avg, size=filled, fee=fee)
            )
            key = (order.market_id, order.side)
            pos = self.positions.get(key) or Position(market_id=order.market_id, side=order.side)
            new_size = pos.size + filled
            pos.avg_price = (pos.avg_price * pos.size + avg * filled) / new_size
            pos.size = new_size
            self.positions[key] = pos

        order.filled = filled
        if filled >= order.size:
            order.status = OrderStatus.FILLED
        elif filled > 0:
            order.status = OrderStatus.PARTIAL
            self.open_orders[order.order_id] = order
        else:
            order.status = OrderStatus.OPEN
            self.open_orders[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        return self.open_orders.pop(order_id, None) is not None

    def get_open_orders(self) -> list[Order]:
        return list(self.open_orders.values())

    def get_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if p.size > 0]

    def get_balance(self) -> float:
        return self.balance

    # --- settlement (called by the runner when a market resolves) -------
    def settle(self, market_id: str, yes_won: bool) -> float:
        """Pay out winning shares; returns realized PnL added to balance."""
        pnl = 0.0
        for (mid, side), pos in list(self.positions.items()):
            if mid != market_id or pos.size <= 0:
                continue
            won = (side == Side.YES) == yes_won
            payout = pos.size if won else 0.0
            pnl += payout - pos.cost
            self.balance += payout
            pos.realized_pnl += payout - pos.cost
            pos.size = 0.0
        return pnl
