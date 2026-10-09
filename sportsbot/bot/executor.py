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
    MarketInfo,
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
        # Called with (bet_id, market, side, entry_price) for every booked
        # fill increment — the runner attaches the markout recorder here.
        self.on_fill = None

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
            # A maker intent was priced to rest; on venues that honour it
            # (Polymarket US participateDontInitiate) it must not become a
            # taker fill at a price the edge math never approved.
            post_only=intent.maker,
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
        self._persist_order(order, intent)

        tracked = _Tracked(order=order, intent=intent,
                           placed_at=datetime.now(timezone.utc))
        if order.filled > 0:
            self._book_fill(tracked, order.filled)
        if order.status in (OrderStatus.OPEN, OrderStatus.PARTIAL):
            self._open[order.client_id] = tracked
        return order

    def _persist_order(self, order: Order, intent: Optional[BetIntent] = None) -> None:
        raw = dict(order.raw or {})
        if intent is not None:
            # Enough of the intent to book a fill after a restart, when the
            # in-memory tracker is gone: the bets row needs sport, model
            # probability and edge, not just the venue's price and size.
            market = intent.market
            raw["intent"] = {
                "sport": market.sport.value if market.sport else "unknown",
                "prob": intent.prob, "edge": intent.edge, "price": intent.price,
                "side": intent.side.value, "market_id": market.market_id,
            }
        self.store.record_order(
            client_id=order.client_id, order_id=order.order_id,
            market_id=order.market_id, side=order.side.value,
            price=order.price, size=order.size, filled=order.filled,
            status=order.status.value, raw=raw,
        )

    def _book_fill(self, tracked: _Tracked, new_total_fill: float) -> None:
        """Record the newly filled increment as bet exposure."""
        increment = new_total_fill - tracked.booked_fill
        if increment <= 0:
            return
        intent = tracked.intent
        market = intent.market
        bet_id = self.store.record_bet(
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
        self._notify_fill(bet_id, market, intent.side, intent.price)

    def _notify_fill(self, bet_id: int, market: MarketInfo, side: Side, price: float) -> None:
        if self.on_fill is None:
            return
        try:
            self.on_fill(bet_id, market, side, price)
        except Exception:
            log.exception("on_fill hook failed (fill is booked regardless)")

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
                # No longer resting: filled, or canceled externally. Ask the
                # venue for the final fill count — guessing either way leaves
                # untracked exposure (if it filled) or phantom exposure (if
                # it was canceled). Venues without get_order keep what was
                # already booked.
                final = self._final_fill(tracked.order)
                if final is not None and final > tracked.order.filled:
                    tracked.order.filled = final
                    self._book_fill(tracked, final)
                tracked.order.status = (OrderStatus.FILLED
                                        if final and final >= tracked.order.size - 1e-9
                                        else OrderStatus.CANCELED)
                self._persist_order(tracked.order)
                log.info("reconcile: order %s left the book (final fill %s)",
                         tracked.order.order_id, final)
                self._open.pop(client_id, None)

    def _final_fill(self, order: Order) -> Optional[float]:
        get_order = getattr(self.exchange, "get_order", None)
        if get_order is None or not order.order_id:
            return None
        try:
            venue = get_order(order.order_id)
        except Exception:
            log.exception("get_order failed for %s", order.order_id)
            return None
        return None if venue is None else float(venue.filled)

    def restore_open_orders(self) -> int:
        """Startup pass over orders the previous process left resting (the
        tracker is in memory only). Any fill the venue reports beyond what was
        booked becomes a bets row; the remainder is canceled, never adopted:
        a resting order whose edge was computed by a dead process is not one
        this process has decided to keep. Returns the number handled."""
        if isinstance(self.exchange, PaperExchange):
            return 0
        n = 0
        for row in self.store.open_orders_rows():
            raw = row.get("raw") or {}
            intent = raw.get("intent") or {}
            order_id = row.get("order_id") or ""
            final = None
            if order_id:
                try:
                    get_order = getattr(self.exchange, "get_order", None)
                    venue = get_order(order_id) if get_order else None
                    final = None if venue is None else float(venue.filled)
                except Exception:
                    log.exception("restore: get_order failed for %s", order_id)
            booked = float(row.get("filled") or 0.0)
            if final is not None and final > booked and intent:
                inc = final - booked
                bet_id = self.store.record_bet(
                    market_id=row["market_id"], sport=intent.get("sport", "unknown"),
                    side=row["side"], model_prob=float(intent.get("prob") or 0.0),
                    entry_price=float(row["price"]),
                    stake=round(float(row["price"]) * inc, 2), size=inc,
                    edge=float(intent.get("edge") or 0.0),
                    exchange=self.exchange.exchange.value, mode=self.mode)
                booked = final
                self._notify_fill(bet_id, MarketInfo(exchange=self.exchange.exchange,
                                                     market_id=row["market_id"]),
                                  Side(row["side"]), float(row["price"]))
            status = "filled"
            if final is None or final < float(row.get("size") or 0.0) - 1e-9:
                ok = self.exchange.cancel_order(order_id or row["client_id"])
                status = "canceled" if ok else "open"   # retried next start
                if not ok:
                    log.warning("restore: cancel failed for %s", order_id)
            self.store.record_order(
                client_id=row["client_id"], order_id=order_id,
                market_id=row["market_id"], side=row["side"], price=row["price"],
                size=row["size"], filled=booked, status=status, raw=raw)
            n += 1
            log.info("restore: order %s -> %s (filled %.2f)", order_id, status, booked)
        return n

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
