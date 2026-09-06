"""Executor: BetIntent -> order on the venue (or paper simulator), with
idempotent submission, TTL cancellation, and full persistence.

Every intent gets exactly one order attempt with a deterministic client id;
on timeout/failure nothing is blindly resubmitted — the next scan cycle
re-evaluates from scratch against fresh state.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sportsbot.core.types import BetIntent, Order, OrderStatus, OrderType, Side
from sportsbot.data.store import Store
from sportsbot.exchanges.base import ExchangeClient
from sportsbot.exchanges.paper import PaperExchange

log = logging.getLogger(__name__)


class Executor:
    def __init__(self, exchange: ExchangeClient, store: Store, mode: str = "paper",
                 order_ttl_seconds: float = 120.0) -> None:
        self.exchange = exchange
        self.store = store
        self.mode = mode
        self.order_ttl = order_ttl_seconds
        self._open: dict[str, tuple[Order, datetime]] = {}

    def submit(self, intent: BetIntent) -> Order:
        market = intent.market
        token_id = market.yes_token_id if intent.side == Side.YES else market.no_token_id
        order = Order(
            client_id=intent.intent_id,     # deterministic: one order per intent
            intent_id=intent.intent_id,
            exchange=self.exchange.exchange,
            market_id=market.market_id,
            token_id=token_id,
            side=intent.side,
            order_type=OrderType.LIMIT,
            price=intent.price,
            size=intent.size,
        )
        log.info(
            "[%s] %s %s %.2f @ %.3f edge=%.3f (%s)",
            self.mode, intent.side.value.upper(), market.slug or market.market_id,
            intent.size, intent.price, intent.edge, intent.reason,
        )
        order = self.exchange.place_order(order)
        self.store.record_order(
            client_id=order.client_id, order_id=order.order_id,
            market_id=order.market_id, side=order.side.value,
            price=order.price, size=order.size, filled=order.filled,
            status=order.status.value, raw=order.raw,
        )
        if order.status in (OrderStatus.OPEN, OrderStatus.PARTIAL):
            self._open[order.client_id] = (order, datetime.now(timezone.utc))
        # Record the bet on any exposure-creating outcome (filled or resting).
        if order.status in (OrderStatus.FILLED, OrderStatus.PARTIAL, OrderStatus.OPEN):
            self.store.record_bet(
                market_id=market.market_id,
                sport=market.sport.value if market.sport else "unknown",
                side=intent.side.value,
                model_prob=intent.prob,
                entry_price=intent.price,
                stake=round(intent.price * intent.size, 2),
                size=intent.size,
                edge=intent.edge,
                exchange=self.exchange.exchange.value,
                mode=self.mode,
            )
        return order

    def expire_stale_orders(self) -> int:
        """Cancel resting orders older than the TTL. Returns count canceled."""
        now = datetime.now(timezone.utc)
        n = 0
        for client_id, (order, placed_at) in list(self._open.items()):
            if now - placed_at < timedelta(seconds=self.order_ttl):
                continue
            ok = self.exchange.cancel_order(order.order_id or client_id)
            if ok:
                order.status = OrderStatus.CANCELED
                self.store.record_order(
                    client_id=order.client_id, order_id=order.order_id,
                    market_id=order.market_id, side=order.side.value,
                    price=order.price, size=order.size, filled=order.filled,
                    status=order.status.value, raw=order.raw,
                )
                n += 1
            self._open.pop(client_id, None)
        return n

    def settle_paper(self, market_id: str, yes_won: bool) -> float:
        if isinstance(self.exchange, PaperExchange):
            return self.exchange.settle(market_id, yes_won)
        return 0.0
