#!/usr/bin/env python3
"""
TENNIS PAPER QUOTER — live measurement of maker viability. Risks $0.

What it does:
- Discovers active Tennis markets (Gamma), subscribes to the live CLOB
  market WebSocket.
- Maintains SIMULATED resting orders: a bid pair (YES at bid touch, NO at
  1-ask touch) per eligible market, refreshed as the book moves.
- Records every event that would have filled a resting order (the touch
  crossing our price), every requote, and the P&L each fill implies —
  including paired lock-ins, one-sided inventory, and taker-unwind costs.
- Writes paper_quotes.csv (events) and paper_fills.csv (economics), plus a
  rolling one-line status to quoter_progress.log.

What it does NOT do:
- Place any real order. There is no order-posting code in this file.
- Model queue priority: a cross to our price counts as a fill. Real fills
  would be a SUBSET of these. Results remain an optimistic upper bound —
  but measured against the live book, tick by tick, which snapshots cannot do.

Config via env (defaults sane): MIN_QUOTE_SPREAD, QUOTE_BAND_LOW/HIGH,
PAIR_TIMEOUT_SECONDS, REQUOTE_TICKS, MAX_ACTIVE_QUOTES, RUNTIME_SECONDS.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import websockets

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

FEE_RATE = float(os.getenv("SPORTS_FEE_RATE", "0.05"))
MIN_QUOTE_SPREAD = float(os.getenv("MIN_QUOTE_SPREAD", "0.02"))
QUOTE_BAND_LOW = float(os.getenv("QUOTE_BAND_LOW", "0.15"))
QUOTE_BAND_HIGH = float(os.getenv("QUOTE_BAND_HIGH", "0.85"))
PAIR_TIMEOUT_SECONDS = float(os.getenv("PAIR_TIMEOUT_SECONDS", "300"))
REQUOTE_TICKS = float(os.getenv("REQUOTE_TICKS", "0.01"))
MAX_ACTIVE_QUOTES = int(os.getenv("MAX_ACTIVE_QUOTES", "40"))
RUNTIME_SECONDS = float(os.getenv("RUNTIME_SECONDS", "21600"))
STAKE = float(os.getenv("PAPER_STAKE", "10.0"))
STATUS_INTERVAL = 5.0

QUOTES_CSV = Path(os.getenv("QUOTES_CSV", "paper_quotes.csv"))
FILLS_CSV = Path(os.getenv("FILLS_CSV", "paper_fills.csv"))
PROGRESS = Path(os.getenv("QUOTER_PROGRESS", "quoter_progress.log"))

TENNIS_TERMS = ("tennis", " atp ", " wta ", "challenger", " itf ", "wimbledon")

logging.basicConfig(
    filename="paper_quoter.log", level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("quoter")


def utc_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def taker_fee(price: float, shares: float) -> float:
    p = min(1.0, max(0.0, price))
    return FEE_RATE * p * (1.0 - p) * shares


@dataclass
class Book:
    bid: float | None = None
    ask: float | None = None

    @property
    def valid(self) -> bool:
        return (
            self.bid is not None and self.ask is not None
            and 0.0 < self.bid < self.ask < 1.0
        )

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass
class QuotePair:
    token_id: str
    question: str
    yes_px: float          # our resting YES bid
    no_px: float           # our resting NO bid == buys at (1 - ask)
    placed: float
    yes_filled_px: float | None = None
    no_filled_px: float | None = None
    requotes: int = 0

    @property
    def both_filled(self) -> bool:
        return self.yes_filled_px is not None and self.no_filled_px is not None


class PaperQuoter:
    def __init__(self) -> None:
        self.books: dict[str, Book] = {}
        self.meta: dict[str, str] = {}
        self.pairs: dict[str, QuotePair] = {}
        self.done: list[tuple[str, float, str]] = []  # (kind, pnl, token)
        self.crossings = 0
        self.messages = 0
        self.start = time.monotonic()
        self._init_csvs()

    def _init_csvs(self) -> None:
        if not QUOTES_CSV.exists():
            with QUOTES_CSV.open("w", newline="") as f:
                csv.writer(f).writerow(
                    ["ts", "token", "event", "side", "our_px", "bid", "ask", "detail"])
        if not FILLS_CSV.exists():
            with FILLS_CSV.open("w", newline="") as f:
                csv.writer(f).writerow(
                    ["ts", "token", "question", "outcome", "spread_captured",
                     "pnl", "held_seconds", "requotes"])

    def log_quote_event(self, token: str, event: str, side: str, px: float,
                        book: Book, detail: str = "") -> None:
        with QUOTES_CSV.open("a", newline="") as f:
            csv.writer(f).writerow(
                [utc_text(), token, event, side, f"{px:.3f}",
                 f"{book.bid:.3f}" if book.bid else "",
                 f"{book.ask:.3f}" if book.ask else "", detail])

    def log_fill(self, pair: QuotePair, outcome: str, spread_cap: float,
                 pnl: float) -> None:
        with FILLS_CSV.open("a", newline="") as f:
            csv.writer(f).writerow(
                [utc_text(), pair.token_id, pair.question[:60], outcome,
                 f"{spread_cap:.3f}", f"{pnl:.4f}",
                 f"{time.monotonic() - pair.placed:.0f}", pair.requotes])
        self.done.append((outcome, pnl, pair.token_id))

    # ---------------- discovery ----------------

    def discover(self) -> None:
        session = requests.Session()
        found = 0
        for offset in range(0, 3000, 100):
            try:
                events = session.get(
                    GAMMA_EVENTS_URL,
                    params={"active": "true", "closed": "false",
                            "limit": 100, "offset": offset},
                    timeout=15,
                ).json()
            except Exception:
                logger.exception("discovery page failed")
                break
            if not events:
                break
            for event in events:
                text = " " + str(event.get("title", "")).lower() + " " + str(
                    event.get("slug", "")).lower() + " "
                if not any(k in text for k in TENNIS_TERMS):
                    continue
                for market in event.get("markets", []):
                    raw = market.get("clobTokenIds")
                    try:
                        ids = json.loads(raw) if isinstance(raw, str) else (raw or [])
                    except Exception:
                        ids = []
                    if len(ids) == 2:
                        token = str(ids[0])
                        self.meta[token] = str(market.get("question", ""))[:80]
                        found += 1
        logger.info("discovered %d tennis tokens", found)

    # ---------------- quoting logic ----------------

    def refresh_quotes(self) -> None:
        now = time.monotonic()
        # expire stale pairs
        for token in list(self.pairs):
            pair = self.pairs[token]
            book = self.books.get(token)
            if now - pair.placed > PAIR_TIMEOUT_SECONDS:
                self._close_pair(pair, book, "timeout")
                del self.pairs[token]
        # place new pairs
        if len(self.pairs) >= MAX_ACTIVE_QUOTES:
            return
        for token, book in self.books.items():
            if token in self.pairs or not book.valid:
                continue
            if book.spread < MIN_QUOTE_SPREAD:
                continue
            if not (QUOTE_BAND_LOW <= book.mid <= QUOTE_BAND_HIGH):
                continue
            pair = QuotePair(
                token_id=token, question=self.meta.get(token, ""),
                yes_px=book.bid, no_px=1.0 - book.ask, placed=now,
            )
            self.pairs[token] = pair
            self.log_quote_event(token, "place", "pair", book.bid, book,
                                 f"no_at={1.0 - book.ask:.3f}")
            if len(self.pairs) >= MAX_ACTIVE_QUOTES:
                break

    def _close_pair(self, pair: QuotePair, book: Book | None, why: str) -> None:
        shares = STAKE
        if pair.both_filled:
            captured = (1.0 - pair.no_filled_px) - pair.yes_filled_px
            pnl = shares * captured
            self.log_fill(pair, "both_locked", captured, pnl)
        elif pair.yes_filled_px is not None:
            exit_px = book.bid if (book and book.valid) else pair.yes_filled_px
            pnl = shares * (exit_px - pair.yes_filled_px) - taker_fee(exit_px, shares)
            self.log_fill(pair, f"one_sided_yes_{why}", 0.0, pnl)
        elif pair.no_filled_px is not None:
            exit_px = (1.0 - book.ask) if (book and book.valid) else pair.no_filled_px
            pnl = shares * (exit_px - pair.no_filled_px) - taker_fee(exit_px, shares)
            self.log_fill(pair, f"one_sided_no_{why}", 0.0, pnl)
        else:
            self.log_fill(pair, f"unfilled_{why}", 0.0, 0.0)

    def on_book_update(self, token: str, book: Book) -> None:
        pair = self.pairs.get(token)
        if pair is None or not book.valid:
            return
        # fill detection: touch crossed to our resting price
        if pair.yes_filled_px is None and book.ask <= pair.yes_px:
            pair.yes_filled_px = pair.yes_px
            self.crossings += 1
            self.log_quote_event(token, "fill", "yes", pair.yes_px, book)
        if pair.no_filled_px is None and (1.0 - book.bid) <= pair.no_px:
            pair.no_filled_px = pair.no_px
            self.crossings += 1
            self.log_quote_event(token, "fill", "no", pair.no_px, book)
        if pair.both_filled:
            self._close_pair(pair, book, "paired")
            del self.pairs[token]
            return
        # requote if the book ran away from us by more than REQUOTE_TICKS
        if pair.yes_filled_px is None and pair.no_filled_px is None:
            if (book.bid - pair.yes_px) >= REQUOTE_TICKS or \
               (pair.yes_px - book.bid) >= REQUOTE_TICKS:
                pair.yes_px = book.bid
                pair.no_px = 1.0 - book.ask
                pair.requotes += 1
                self.log_quote_event(token, "requote", "pair", book.bid, book)

    # ---------------- websocket ----------------

    async def ws_loop(self) -> None:
        tokens = list(self.meta)[:950]
        if not tokens:
            logger.error("no tennis tokens to subscribe")
            return
        sub = json.dumps({"assets_ids": tokens, "type": "market"})
        while time.monotonic() - self.start < RUNTIME_SECONDS:
            try:
                async with websockets.connect(MARKET_WS_URL, ping_interval=10) as ws:
                    await ws.send(sub)
                    async for raw in ws:
                        self.messages += 1
                        self.handle(raw)
                        if time.monotonic() - self.start > RUNTIME_SECONDS:
                            break
            except Exception:
                logger.exception("ws reconnect in 5s")
                await asyncio.sleep(5)

    def handle(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
        except Exception:
            return
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            token = str(item.get("asset_id", ""))
            if not token:
                continue
            book = self.books.setdefault(token, Book())
            event = item.get("event_type")
            if event == "book":
                bids = item.get("bids") or item.get("buys") or []
                asks = item.get("asks") or item.get("sells") or []
                try:
                    if bids:
                        book.bid = max(float(level["price"]) for level in bids)
                    if asks:
                        book.ask = min(float(level["price"]) for level in asks)
                except Exception:
                    return
            elif event == "price_change":
                for change in item.get("changes", []):
                    try:
                        price = float(change.get("price"))
                        size = float(change.get("size", 0))
                        side = str(change.get("side", "")).upper()
                    except Exception:
                        continue
                    if side == "BUY":
                        if size > 0 and (book.bid is None or price > book.bid):
                            book.bid = price
                        elif size == 0 and book.bid is not None and price >= book.bid:
                            book.bid = None
                    elif side == "SELL":
                        if size > 0 and (book.ask is None or price < book.ask):
                            book.ask = price
                        elif size == 0 and book.ask is not None and price <= book.ask:
                            book.ask = None
            self.on_book_update(token, book)

    # ---------------- status ----------------

    async def status_loop(self) -> None:
        while time.monotonic() - self.start < RUNTIME_SECONDS:
            self.refresh_quotes()
            outcomes = defaultdict(int)
            pnl = 0.0
            for kind, p, _ in self.done:
                key = "locked" if kind == "both_locked" else (
                    "unfilled" if kind.startswith("unfilled") else "one_sided")
                outcomes[key] += 1
                pnl += p
            line = (
                f"{utc_text()} quoting={len(self.pairs)} msgs={self.messages} "
                f"crossings={self.crossings} locked={outcomes['locked']} "
                f"one_sided={outcomes['one_sided']} unfilled={outcomes['unfilled']} "
                f"paper_pnl=${pnl:+.2f}"
            )
            with PROGRESS.open("a") as handle:
                handle.write(line + "\n")
            await asyncio.sleep(STATUS_INTERVAL)
        # session end: close out remaining pairs
        for token in list(self.pairs):
            self._close_pair(self.pairs[token], self.books.get(token), "session_end")
            del self.pairs[token]

    async def run(self) -> None:
        self.discover()
        await asyncio.gather(self.ws_loop(), self.status_loop())


if __name__ == "__main__":
    print("Paper quoter starting — $0 at risk, no order code present.")
    print(f"Progress: tail -F {PROGRESS}")
    asyncio.run(PaperQuoter().run())
