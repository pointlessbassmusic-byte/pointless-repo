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

    # --- simulated execution -------------------------------------------
    def place_order(self, order: Order, quote: Optional[MarketQuote] = None) -> Order:
        order.exchange = Exchange.PAPER
        order.order_id = f"paper-{order.client_id[:12]}"

        if quote is None and self.data_client is not None:
            try:
                quote = self.get_quote(
                    MarketInfo(
                        exchange=self.exchange,
                        market_id=order.market_id,
                        yes_token_id=order.token_id,
                    )
                )
            except Exception as exc:
                log.warning("paper: no quote for %s (%s); order rests", order.market_id, exc)

        filled = 0.0
        cost = 0.0
        if quote is not None:
            # Buying YES walks the YES asks; buying NO walks (1 - bid) levels.
            if order.side == Side.YES:
                levels = [(lvl.price, lvl.size) for lvl in quote.asks]
            else:
                levels = [(round(1.0 - lvl.price, 6), lvl.size) for lvl in quote.bids]
            for price, size in levels:
                if price > order.price or filled >= order.size:
                    break
                take = min(size, order.size - filled)
                filled += take
                cost += take * price

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
