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

from sportsbot.bot.portfolio import arm_of_intent
from sportsbot.core.books import tape_fill_size
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
    lookups_failed: int = 0    # get_order failures after it left the book
    first_fill_at: Optional[datetime] = None


MAX_FINAL_LOOKUPS = 10         # cycles to keep asking the venue for a final fill


def _book_snapshot(quote: Optional[MarketQuote]) -> Optional[dict]:
    if quote is None:
        return None
    return {"bid": quote.bid, "ask": quote.ask,
            "bid_size": quote.bids[0].size if quote.bids else None,
            "ask_size": quote.asks[0].size if quote.asks else None}


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
        placed_at = datetime.now(timezone.utc)
        sport = market.sport.value if market.sport else "unknown"
        # What the maker test needs per order (docs/MAKER_TEST.md): which
        # arm posted it, the book it saw, and when. Persisted in `raw`.
        order.raw = {
            "arm": arm_of_intent(sport, intent.reason),
            "reason": (intent.reason or "")[:120],
            "placed_at": placed_at.isoformat(),
            "start_time": market.start_time.isoformat() if market.start_time else None,
            "book": _book_snapshot(quote),
        }
        # Post-only if the intent was priced to rest OR the arm is a maker
        # arm: either reason alone forbids crossing, and the flag can only
        # make the venue reject an order, never fill a worse one.
        order.post_only = bool(intent.maker) or str(order.raw["arm"]).endswith("/maker")
        if isinstance(self.exchange, PaperExchange):
            order = self.exchange.place_order(order, quote=quote, market=market,
                                              now=placed_at)
        else:
            order = self.exchange.place_order(order)
        self._persist_order(order, intent)

        tracked = _Tracked(order=order, intent=intent, placed_at=placed_at)
        if order.filled > 0:
            self._note_fill(tracked, placed_at)
            self._book_fill(tracked, order.filled)
        if order.status in (OrderStatus.OPEN, OrderStatus.PARTIAL):
            self._open[order.client_id] = tracked
        return order

    def _persist_order(self, order: Order, intent: Optional[BetIntent] = None) -> None:
        if intent is not None:
            # Enough of the intent to book a fill after a restart, when the
            # in-memory tracker is gone: the bets row needs sport, model
            # probability and edge, not just the venue's price and size. Kept
            # on the order itself so every later persist carries it too.
            market = intent.market
            order.raw = {**(order.raw or {}), "intent": {
                "sport": market.sport.value if market.sport else "unknown",
                "prob": intent.prob, "edge": intent.edge, "price": intent.price,
                "side": intent.side.value, "market_id": market.market_id,
            }}
        raw = dict(order.raw or {})
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
        sport = market.sport.value if market.sport else "unknown"
        bet_id = self.store.record_bet(
            market_id=market.market_id,
            sport=sport,
            side=intent.side.value,
            model_prob=intent.prob,
            entry_price=intent.price,
            stake=round(intent.price * increment, 2),
            size=increment,
            edge=intent.edge,
            exchange=self.exchange.exchange.value,
            mode=self.mode,
            arm=arm_of_intent(sport, intent.reason),
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

    def _note_fill(self, tracked: _Tracked, now: Optional[datetime] = None) -> None:
        """First time an order shows a fill: remember when, and how long
        it rested, on the order itself (the maker test reads it back)."""
        if tracked.first_fill_at is not None:
            return
        now = now or datetime.now(timezone.utc)
        tracked.first_fill_at = now
        tracked.order.raw = {**tracked.order.raw, "first_fill_at": now.isoformat(),
                             "rested_seconds": round((now - tracked.placed_at).total_seconds(), 1)}

    def _tape_client(self):
        ex = self.exchange
        return ex.data_client if isinstance(ex, PaperExchange) else ex

    def _tape_shadow(self, tracked: _Tracked, now: Optional[datetime] = None) -> None:
        """Apply the paper venue's fill rule to a LIVE order after the fact:
        how much the public tape printed through its level while it rested
        (bounded by the TTL and the game start). The maker test's fill
        share is real fills divided by this, order by order. Stored on the
        order as `tape_through`; failures leave it absent, never guessed."""
        now = now or datetime.now(timezone.utc)
        order = tracked.order
        market = tracked.intent.market
        before = min(now, tracked.placed_at + timedelta(seconds=self.order_ttl))
        if market.start_time is not None:
            start = market.start_time
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            before = min(before, start)
        client = self._tape_client()
        if client is None or before <= tracked.placed_at:
            return
        try:
            prints = client.recent_trades(market, tracked.placed_at)
        except Exception:
            log.exception("tape shadow failed for %s", order.market_id)
            return
        through = tape_fill_size(prints, order.side, order.price,
                                 after=tracked.placed_at, before=before)
        order.raw = {**order.raw, "tape_through": round(min(order.size, through), 6),
                     "tape_prints": len(prints),
                     "rested_seconds": order.raw.get(
                         "rested_seconds",
                         round((before - tracked.placed_at).total_seconds(), 1))}

    def resting_stake_by_sport(self) -> dict[str, float]:
        """Unfilled stake (price x remaining size) of tracked resting orders,
        per sport key. The touch arm counts it against the sport budget so
        the orders that could fill never exceed what the book can carry."""
        out: dict[str, float] = {}
        for t in self._open.values():
            sport = t.intent.market.sport
            key = sport.value if sport else "unknown"
            remaining = max(0.0, float(t.order.size) - float(t.order.filled or 0.0))
            out[key] = out.get(key, 0.0) + float(t.order.price) * remaining
        return out

    def resting_sides(self, market_id: str) -> set[Side]:
        """Sides with a tracked resting order on `market_id` (so a quoting
        arm does not stack orders on a side it already rests)."""
        return {t.order.side for t in self._open.values()
                if t.order.market_id == market_id}

    # ------------------------------------------------------------------
    def reconcile_open_orders(self) -> None:
        """Refresh venue state for tracked resting orders; book any fills
        that happened since the last cycle (live maker fills). Paper
        resting orders fill only when the venue's public tape has printed
        through them (`PaperExchange.reconcile_resting`); those fills are
        booked as bets the same way live maker fills are."""
        if not self._open:
            return
        if isinstance(self.exchange, PaperExchange):
            for order in self.exchange.reconcile_resting(ttl_seconds=self.order_ttl):
                tracked = self._open.get(order.client_id)
                if tracked is None:
                    continue
                self._note_fill(tracked)
                self._book_fill(tracked, order.filled)
                if order.status == OrderStatus.FILLED:
                    self._tape_shadow(tracked)
                    self._open.pop(order.client_id, None)
                self._persist_order(order)
            return
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
                    self._note_fill(tracked)
                    self._book_fill(tracked, venue.filled)
                    self._persist_order(tracked.order, tracked.intent)
            else:
                # No longer resting: filled, or canceled externally. Ask the
                # venue for the final fill count — guessing either way leaves
                # untracked exposure (if it filled) or phantom exposure (if
                # it was canceled).
                has_lookup = getattr(self.exchange, "get_order", None) is not None
                final = self._final_fill(tracked.order) if has_lookup else None
                if final is None and has_lookup:
                    # A transient lookup failure must not turn a filled order
                    # into "canceled": keep asking, up to a bound.
                    tracked.lookups_failed += 1
                    if tracked.lookups_failed < MAX_FINAL_LOOKUPS:
                        log.warning("reconcile: order %s left the book, final fill "
                                    "unknown (lookup %d/%d); retrying next cycle",
                                    tracked.order.order_id, tracked.lookups_failed,
                                    MAX_FINAL_LOOKUPS)
                        continue
                    log.error("reconcile: order %s final fill unknown after %d lookups; "
                              "keeping booked exposure only — RECONCILE MANUALLY",
                              tracked.order.order_id, MAX_FINAL_LOOKUPS)
                if final is not None and final > tracked.order.filled:
                    tracked.order.filled = final
                    self._note_fill(tracked)
                    self._book_fill(tracked, final)
                tracked.order.status = (OrderStatus.FILLED
                                        if final and final >= tracked.order.size - 1e-9
                                        else OrderStatus.CANCELED)
                # The tape shadow is taken now, while the window it rested
                # in is still recent (the maker test's fill-share denominator).
                self._tape_shadow(tracked)
                self._persist_order(tracked.order, tracked.intent)
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
            if final is not None and final > booked:
                inc = final - booked
                if not intent:
                    # Pre-upgrade row with no intent: the exposure is real
                    # whatever we know about why it was placed, so book it
                    # with unknown provenance rather than lose it.
                    log.error("restore: order %s filled %.2f beyond booked with no stored "
                              "intent; booking as sport=unknown", order_id, inc)
                bet_id = self.store.record_bet(
                    market_id=row["market_id"], sport=intent.get("sport", "unknown"),
                    side=row["side"], model_prob=float(intent.get("prob") or 0.0),
                    entry_price=float(row["price"]),
                    stake=round(float(row["price"]) * inc, 2), size=inc,
                    edge=float(intent.get("edge") or 0.0),
                    exchange=self.exchange.exchange.value, mode=self.mode,
                    arm=raw.get("arm") or arm_of_intent(intent.get("sport", "unknown"),
                                                        raw.get("reason")))
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
                self._tape_shadow(tracked, now)
                self._persist_order(order, tracked.intent)
                self._open.pop(client_id, None)
                n += 1
            else:
                log.warning("cancel failed for %s; will retry next cycle",
                            order.order_id or client_id)
        return n

    RESTING_STATUSES = ("pending", "open", "partial")

    def cancel_all_resting(self, reason: str = "shutdown") -> int:
        """Cancel every tracked resting order now, TTL or not, and record why
        (`raw.canceled_reason`). The run loop calls this on SIGTERM so a
        restart never strands a paper order in memory or a live order on
        the venue. Each order still gets its tape shadow for the window it
        actually rested. A failed cancel stays tracked and is reported."""
        now = datetime.now(timezone.utc)
        n = 0
        for client_id, tracked in list(self._open.items()):
            order = tracked.order
            ok = self.exchange.cancel_order(order.order_id or client_id)
            if not ok:
                log.warning("shutdown cancel failed for %s", order.order_id or client_id)
                continue
            order.status = OrderStatus.CANCELED
            order.raw = {**(order.raw or {}), "canceled_reason": reason}
            self._tape_shadow(tracked, now)
            self._persist_order(order)
            self._open.pop(client_id, None)
            n += 1
        return n

    def sweep_orphans(self) -> int:
        """Startup: order rows the store still shows resting from a previous
        process. Nothing in memory tracks them, so they would stay `open`
        forever and the maker report would count them as live orders. Paper
        rows are marked canceled/orphaned (the paper book they rested on
        died with the process). In live mode the venue is asked for its
        resting orders first and ours are cancelled there before the row is
        closed; a venue order the store does not know is logged, never
        touched. Orphaned rows carry no tape shadow (no market context) and
        the report excludes them from the fill share."""
        rows = [o for o in self.store.all_orders() if o.get("status") in self.RESTING_STATUSES
                and o.get("client_id") not in self._open]
        if not rows:
            return 0
        venue_open: dict[str, Order] = {}
        if self.mode == "live":
            try:
                for vo in self.exchange.get_open_orders():
                    venue_open[vo.order_id] = vo
                    venue_open[vo.client_id] = vo
            except Exception:
                log.exception("sweep: venue open-orders lookup failed; closing rows locally only")
        known_ids = {o.get("order_id") for o in rows} | {o.get("client_id") for o in rows}
        for oid, vo in venue_open.items():
            if oid not in known_ids:
                log.warning("sweep: venue holds resting order %s the store does not know; left alone", oid)
        n = 0
        for o in rows:
            vo = venue_open.get(o.get("order_id") or "") or venue_open.get(o.get("client_id") or "")
            if vo is not None:
                if not self.exchange.cancel_order(vo.order_id or vo.client_id):
                    log.warning("sweep: venue cancel failed for %s; row left resting for retry", vo.order_id)
                    continue
            raw = dict(o.get("raw") or {})
            raw.update({"orphaned": True, "canceled_reason": "orphaned_on_restart",
                        "orphaned_at": datetime.now(timezone.utc).isoformat()})
            self.store.record_order(
                client_id=o["client_id"], order_id=o.get("order_id") or "", market_id=o["market_id"],
                side=o["side"], price=float(o["price"]), size=float(o["size"]),
                filled=float(o.get("filled") or 0.0), status=OrderStatus.CANCELED.value, raw=raw)
            n += 1
        if n:
            log.warning("sweep: closed %d order row(s) orphaned by a previous process", n)
        return n

    def settle_paper(self, market_id: str, yes_won: bool) -> float:
        if isinstance(self.exchange, PaperExchange):
            return self.exchange.settle(market_id, yes_won)
        return 0.0
