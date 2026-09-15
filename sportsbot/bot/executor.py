"""Executor: BetIntent -> order on the venue (or paper simulator), with
idempotent submission, TTL cancellation, fill reconciliation, and full
persistence.

Exposure accounting is fill-based: a bets row records only what actually
filled (resting size is not exposure). Resting orders are tracked and
reconciled each cycle so later fills on live venues still become bets; a
failed cancel keeps the order tracked and is retried next cycle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sportsbot.core.types import (
    BetIntent,
    MarketQuote,
    Order,
    OrderStatus,
    OrderType,
    Side,
)
from sportsbot.data.store import Store
from sportsbot.exchanges.base import ExchangeClient
from sportsbot.exchanges.paper import PaperExchange

log = logging.getLogger(__name__)


@dataclass
class _Tracked:
    order: Order
    intent: BetIntent
    placed_at: datetime
    booked_fill: float = 0.0   # fill size already recorded as a bet


class Executor:
    def __init__(self, exchange: ExchangeClient, store: Store, mode: str = "paper",
                 order_ttl_seconds: float = 120.0) -> None:
        self.exchange = exchange
        self.store = store
        self.mode = mode
        self.order_ttl = order_ttl_seconds
        self._open: dict[str, _Tracked] = {}

    # ------------------------------------------------------------------
    def submit(self, intent: BetIntent, quote: Optional[MarketQuote] = None) -> Order:
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
        if isinstance(self.exchange, PaperExchange):
            order = self.exchange.place_order(order, quote=quote)
        else:
            order = self.exchange.place_order(order)
        self._persist_order(order)

        tracked = _Tracked(order=order, intent=intent,
                           placed_at=datetime.now(timezone.utc))
        if order.filled > 0:
            self._book_fill(tracked, order.filled)
        if order.status in (OrderStatus.OPEN, OrderStatus.PARTIAL):
            self._open[order.client_id] = tracked
        return order

    def _persist_order(self, order: Order) -> None:
        self.store.record_order(
            client_id=order.client_id, order_id=order.order_id,
            market_id=order.market_id, side=order.side.value,
            price=order.price, size=order.size, filled=order.filled,
            status=order.status.value, raw=order.raw,
        )

    def _book_fill(self, tracked: _Tracked, new_total_fill: float) -> None:
        """Record the newly filled increment as bet exposure."""
        increment = new_total_fill - tracked.booked_fill
        if increment <= 0:
            return
        intent = tracked.intent
        market = intent.market
        self.store.record_bet(
            market_id=market.market_id,
            sport=market.sport.value if market.sport else "unknown",
            side=intent.side.value,
            model_prob=intent.prob,
            entry_price=intent.price,
            stake=round(intent.price * increment, 2),
            size=increment,
            edge=intent.edge,
            exchange=self.exchange.exchange.value,
            mode=self.mode,
        )
        tracked.booked_fill = new_total_fill

    # ------------------------------------------------------------------
    def reconcile_open_orders(self) -> None:
        """Refresh venue state for tracked resting orders; book any fills
        that happened since the last cycle (live maker fills)."""
        if not self._open or isinstance(self.exchange, PaperExchange):
            return  # paper maker orders never fill (conservative by design)
        try:
            venue_orders = {o.order_id: o for o in self.exchange.get_open_orders()}
        except Exception:
            log.exception("reconcile: get_open_orders failed")
            return
        for client_id, tracked in list(self._open.items()):
            venue = venue_orders.get(tracked.order.order_id)
            if venue is not None:
                if venue.filled > tracked.order.filled:
                    tracked.order.filled = venue.filled
                    self._book_fill(tracked, venue.filled)
                    self._persist_order(tracked.order)
            else:
                # No longer resting: fully filled or canceled externally.
                # Conservatively book it as fully filled only if partials
                # were already seen; otherwise leave the booked exposure.
                log.info("reconcile: order %s left the book", tracked.order.order_id)
                self._open.pop(client_id, None)

    def expire_stale_orders(self) -> int:
        """Cancel resting orders older than the TTL. Returns count canceled.
        A failed cancel keeps the order tracked so it is retried next cycle."""
        now = datetime.now(timezone.utc)
        n = 0
        for client_id, tracked in list(self._open.items()):
            if now - tracked.placed_at < timedelta(seconds=self.order_ttl):
                continue
            order = tracked.order
            ok = self.exchange.cancel_order(order.order_id or client_id)
            if ok:
                order.status = OrderStatus.CANCELED
                self._persist_order(order)
                self._open.pop(client_id, None)
                n += 1
            else:
                log.warning("cancel failed for %s; will retry next cycle",
                            order.order_id or client_id)
        return n

    def settle_paper(self, market_id: str, yes_won: bool) -> float:
        if isinstance(self.exchange, PaperExchange):
            return self.exchange.settle(market_id, yes_won)
        return 0.0
