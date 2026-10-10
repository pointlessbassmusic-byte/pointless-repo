"""Paper-trading venue: wraps a real client's *market data* while simulating
order fills locally. This is the default mode — identical code path to live
except `place_order` never leaves the process.

Fill model (conservative):
* A limit BUY fills immediately only up to the size available at or below
  our price on the real book (walking levels), paying each level's price.
* The remainder rests. A resting order fills ONLY when the venue's public
  taker tape later prints THROUGH its limit (`reconcile_resting`): a YES
  bid at L on a print at or below L, a NO bid at L on a YES print at or
  above 1 - L, for the size that printed. Nothing is assumed about the
  queue beyond that, which keeps this an upper bound on real maker fills
  (front-of-queue is implied) while charging the maker fee and recording
  the adverse selection a maker actually eats: the fill happens exactly
  when price moves through the order. Without a data client, or on a
  venue with no tape, resting orders never fill.
* Taker fees are charged with the venue's fee model; maker fills pay
  `maker_fee_fn` (0 on Polymarket, Kalshi's per-contract maker charge).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sportsbot.core.books import buy_levels, sell_levels, tape_fill_size, walk_book, walk_sell
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

FeeFn = Callable[..., float]


class PaperExchange(ExchangeClient):
    exchange = Exchange.PAPER

    def __init__(
        self,
        data_client: Optional[ExchangeClient] = None,
        starting_balance: float = 1000.0,
        fee_fn: Optional[FeeFn] = None,
        maker_fee_fn: Optional[FeeFn] = None,
    ) -> None:
        self.data_client = data_client
        self.balance = starting_balance
        self.fee_fn = fee_fn or (lambda price, size, market_id=None: 0.0)
        self.maker_fee_fn = maker_fee_fn or (lambda price, size, market_id=None: 0.0)
        self.open_orders: dict[str, Order] = {}
        self.fills: list[Fill] = []
        self.positions: dict[tuple[str, Side], Position] = {}
        # resting-order context the tape check needs: the market (token ids
        # for the Polymarket frame flip), when it went on the book, and how
        # much had filled against the book at placement.
        self._resting: dict[str, tuple[MarketInfo, datetime, float]] = {}

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
    def place_order(self, order: Order, quote: Optional[MarketQuote] = None,
                    market: Optional[MarketInfo] = None,
                    now: Optional[datetime] = None) -> Order:
        """`quote` must be the YES-frame book the caller just evaluated
        (Executor forwards it). Without a quote the order simply rests —
        conservatively, nothing is assumed to fill. `market` is kept for
        the tape check on the resting remainder; without it, a minimal
        MarketInfo is built from the order (enough for Kalshi's ticker and
        for the one-token Polymarket frame rule)."""
        order.exchange = Exchange.PAPER
        order.order_id = f"paper-{order.client_id[:12]}"
        now = now or datetime.now(timezone.utc)

        if quote is None:
            log.warning("paper: no quote provided for %s; order rests unfilled",
                        order.market_id)

        filled = 0.0
        cost = 0.0
        if quote is not None:
            avg, filled = walk_book(buy_levels(quote, order.side), order.price, order.size)
            cost = avg * filled

        if filled > 0:
            fee = self.fee_fn(cost / filled, filled, order.market_id)
            total = cost + fee
            if total > self.balance:
                order.status = OrderStatus.REJECTED
                order.raw = {"error": "insufficient paper balance"}
                return order
            self.balance -= total
            avg = cost / filled
            self._book(order, avg, filled, fee)

        order.filled = filled
        if filled >= order.size:
            order.status = OrderStatus.FILLED
        else:
            order.status = OrderStatus.PARTIAL if filled > 0 else OrderStatus.OPEN
            self.open_orders[order.order_id] = order
            self._resting[order.order_id] = (market or self._market_from(order), now, filled)
        return order

    def _market_from(self, order: Order) -> MarketInfo:
        venue = self.data_client.exchange if self.data_client is not None else Exchange.PAPER
        return MarketInfo(exchange=venue, market_id=order.market_id,
                          yes_token_id=order.token_id if order.side == Side.YES else None,
                          no_token_id=order.token_id if order.side == Side.NO else None)

    def _book(self, order: Order, price: float, size: float, fee: float) -> None:
        self.fills.append(
            Fill(order_id=order.order_id, market_id=order.market_id,
                 side=order.side, price=price, size=size, fee=fee)
        )
        key = (order.market_id, order.side)
        pos = self.positions.get(key) or Position(market_id=order.market_id, side=order.side)
        new_size = pos.size + size
        pos.avg_price = (pos.avg_price * pos.size + price * size) / new_size
        pos.size = new_size
        self.positions[key] = pos

    def reconcile_resting(self, now: Optional[datetime] = None,
                          ttl_seconds: Optional[float] = None) -> list[Order]:
        """Fill resting orders the public tape has printed through since
        they were placed. Returns the orders whose fill changed; a fully
        filled order leaves `open_orders`. One tape read per market per
        call; a venue without a tape (or no data client) fills nothing.

        The window closes at the earliest of now, placement + `ttl_seconds`
        (the executor's cancel deadline: a print after it would have met a
        cancelled order) and the market's start time (pre-game orders do
        not rest into the game). Without the bound, a reconcile that runs
        long after the TTL -- the daily one-cycle sim -- would credit a
        whole day of prints to a two-minute order."""
        if self.data_client is None or not self.open_orders:
            return []
        now = now or datetime.now(timezone.utc)
        tapes: dict[str, Optional[list]] = {}
        changed: list[Order] = []
        for order_id, order in list(self.open_orders.items()):
            ctx = self._resting.get(order_id)
            if ctx is None:
                continue
            market, placed_at, filled0 = ctx
            if market.market_id not in tapes:
                try:
                    tapes[market.market_id] = self.data_client.recent_trades(market, placed_at)
                except Exception:
                    log.exception("paper: tape read failed for %s", market.market_id)
                    tapes[market.market_id] = None
            prints = tapes[market.market_id]
            if not prints:
                continue
            before = now
            if ttl_seconds is not None:
                before = min(before, placed_at + timedelta(seconds=float(ttl_seconds)))
            if market.start_time is not None:
                start = market.start_time
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                before = min(before, start)
            if before <= placed_at:
                continue
            through = tape_fill_size(prints, order.side, order.price, after=placed_at, before=before)
            target = min(order.size, filled0 + through)
            increment = target - order.filled
            if increment <= 1e-9:
                continue
            fee = self.maker_fee_fn(order.price, increment, order.market_id)
            total = order.price * increment + fee
            if total > self.balance:
                log.warning("paper: tape fill of %.2f on %s skipped, balance %.2f < %.2f",
                            increment, order.market_id, self.balance, total)
                continue
            self.balance -= total
            self._book(order, order.price, increment, fee)
            order.filled = target
            order.updated_at = now
            order.raw = {**order.raw, "tape_filled": round(target - filled0, 6)}
            if order.filled >= order.size - 1e-9:
                order.status = OrderStatus.FILLED
                self.open_orders.pop(order_id, None)
                self._resting.pop(order_id, None)
            else:
                order.status = OrderStatus.PARTIAL
            changed.append(order)
            log.info("paper: tape printed through %s %s @ %.3f: +%.2f (%.2f/%.2f)",
                     order.side.value.upper(), order.market_id, order.price,
                     increment, order.filled, order.size)
        return changed

    def cancel_order(self, order_id: str) -> bool:
        self._resting.pop(order_id, None)
        return self.open_orders.pop(order_id, None) is not None

    def get_open_orders(self) -> list[Order]:
        return list(self.open_orders.values())

    def get_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if p.size > 0]

    def get_balance(self) -> float:
        return self.balance

    # --- early close (called by the runner's position manager) ----------
    def close_position(self, market_id: str, side: Side, quote: MarketQuote,
                       min_price: float = 0.02) -> Optional[dict]:
        """Sell an open position into the current book (conservative walk of
        the real levels; taker fee charged). Returns
        {closed_size, avg_price, proceeds, fee} or None if nothing sellable."""
        pos = self.positions.get((market_id, side))
        if pos is None or pos.size <= 0:
            return None
        avg, sold = walk_sell(sell_levels(quote, side), min_price, pos.size)
        if sold <= 0 or avg <= 0:
            return None
        fee = self.fee_fn(avg, sold, market_id)
        proceeds = avg * sold - fee
        self.balance += proceeds
        pos.realized_pnl += proceeds - pos.avg_price * sold
        pos.size -= sold
        self.fills.append(
            Fill(order_id=f"paper-close-{market_id[:12]}", market_id=market_id,
                 side=side, price=avg, size=-sold, fee=fee)
        )
        return {"closed_size": sold, "avg_price": avg,
                "proceeds": round(proceeds, 4), "fee": round(fee, 4)}

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
