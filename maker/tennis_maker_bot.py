#!/usr/bin/env python3
"""
tennis_maker_bot.py — PAPER-ONLY maker quoting on Polymarket tennis MATCH markets,
with full L2 order-book WebSocket capture and a conservative queue-position fill model.

What this answers (the only open thesis from the retro/forward tests):
  "Do resting maker orders on live tennis match markets actually fill, and is the
   spread + rebate capture net-positive after adverse selection?"

Hard properties:
  - PAPER ONLY. There is no live order path in this file. No keys are read, ever.
  - Records every L2 event (book snapshots, deltas, trades, tick changes) to
    gzipped JSONL for offline replay — this dataset is valuable regardless of PnL.
  - Fill simulation is CONSERVATIVE:
      * queue_ahead = displayed size already at our price when we place/join
      * only real printed trade volume at our price (correct aggressor side)
        works off the queue; level shrinkage from cancels does NOT help us
      * trades strictly through our price fill us
      * repricing resets queue position
    => paper fills here are a LOWER bound on optimism vs. the old snapshot method.
  - Quotes only: live match markets (gameStartTime-gated, futures excluded),
    mid in [MIN_MID, MAX_MID], spread >= MIN_SPREAD_TICKS.
  - Maker entries and maker take-profits pay no fee and earn the sports rebate
    (modeled, rate configurable); stop-losses and time-flattens are TAKER exits
    with the full fee modeled — the hybrid maker-entry/taker-stop shape.

Usage:
  python3 tennis_maker_bot.py --self-test     # offline logic tests, no network
  python3 tennis_maker_bot.py                 # run paper session

Env (all optional, sane defaults):
  MAX_RUNTIME_SECONDS=21600  DISCOVERY_INTERVAL_SECONDS=300
  MIN_MID=0.15 MAX_MID=0.85  MIN_SPREAD_TICKS=2
  QUOTE_SIZE_USD=5  MAX_INVENTORY_USD_PER_TOKEN=10  MAX_TOTAL_INVENTORY_USD=40
  ORDER_TTL_SECONDS=180  POSITION_TTL_SECONDS=900
  TP_TICKS=2  SL_TICKS=4
  FEE_RATE=0.05  REBATE_RATE=0.15
  GAME_LOOKAHEAD_HOURS=12  GAME_LOOKBACK_HOURS=8
  L2_LOG=l2_events.jsonl.gz  TRADE_CSV=paper_maker_trades.csv
  LOG_FILE=tennis_maker_bot.log  DASHBOARD_INTERVAL_SECONDS=30
"""

from __future__ import annotations

import asyncio
import csv
import gzip
import json
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # self-test can run without it
    requests = None

GAMMA_URL = "https://gamma-api.polymarket.com/events"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

log = logging.getLogger("tennis-maker")


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class Config:
    max_runtime: int = _i("MAX_RUNTIME_SECONDS", 21600)
    discovery_interval: int = _i("DISCOVERY_INTERVAL_SECONDS", 300)
    min_mid: float = _f("MIN_MID", 0.15)
    max_mid: float = _f("MAX_MID", 0.85)
    min_spread_ticks: int = _i("MIN_SPREAD_TICKS", 2)
    quote_size_usd: float = _f("QUOTE_SIZE_USD", 5.0)
    max_inv_token: float = _f("MAX_INVENTORY_USD_PER_TOKEN", 10.0)
    max_inv_total: float = _f("MAX_TOTAL_INVENTORY_USD", 40.0)
    order_ttl: int = _i("ORDER_TTL_SECONDS", 180)
    position_ttl: int = _i("POSITION_TTL_SECONDS", 900)
    tp_ticks: int = _i("TP_TICKS", 2)
    sl_ticks: int = _i("SL_TICKS", 4)
    fee_rate: float = _f("FEE_RATE", 0.05)
    rebate_rate: float = _f("REBATE_RATE", 0.15)
    lookahead_h: float = _f("GAME_LOOKAHEAD_HOURS", 12.0)
    lookback_h: float = _f("GAME_LOOKBACK_HOURS", 8.0)
    l2_log: str = os.getenv("L2_LOG", "l2_events.jsonl.gz")
    trade_csv: str = os.getenv("TRADE_CSV", "paper_maker_trades.csv")
    log_file: str = os.getenv("LOG_FILE", "tennis_maker_bot.log")
    dashboard_interval: int = _i("DASHBOARD_INTERVAL_SECONDS", 30)


CFG = Config()

# Season-long futures / outrights — never quote these.
FUTURES_RE = re.compile(
    r"(win the .*(open|wimbledon|slam|masters|finals|title|cup|20\d\d))|"
    r"(champion)|(to win 20\d\d)|(year.end)|(number one)|(#1)",
    re.IGNORECASE,
)


def taker_fee(price: float, shares: float, rate: float = None) -> float:
    r = CFG.fee_rate if rate is None else rate
    return r * price * (1.0 - price) * shares


def maker_rebate(price: float, shares: float) -> float:
    # Modeled as REBATE_RATE share of the taker fee on the same notional.
    # Verify the live rebate mechanics at docs.polymarket.com before trusting
    # rebate-dependent conclusions; set REBATE_RATE=0 for the pessimistic view.
    return CFG.rebate_rate * taker_fee(price, shares)


# --------------------------------------------------------------------------
# Order book
# --------------------------------------------------------------------------

class OrderBook:
    __slots__ = ("bids", "asks", "tick", "last_update")

    def __init__(self) -> None:
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.tick: float = 0.01
        self.last_update: float = 0.0

    def apply_snapshot(self, bids: List[dict], asks: List[dict]) -> None:
        self.bids = {float(x["price"]): float(x["size"]) for x in bids}
        self.asks = {float(x["price"]): float(x["size"]) for x in asks}
        self.last_update = time.time()
        self._infer_tick()

    def apply_change(self, price: float, side: str, size: float) -> None:
        book = self.bids if side.upper() == "BUY" else self.asks
        if size <= 0:
            book.pop(price, None)
        else:
            book[price] = size
        self.last_update = time.time()

    def _infer_tick(self) -> None:
        for p in list(self.bids) + list(self.asks):
            s = f"{p:.4f}".rstrip("0")
            if len(s.split(".")[-1]) >= 3:
                self.tick = 0.001
                return

    def best_bid(self) -> Optional[float]:
        return max(self.bids) if self.bids else None

    def best_ask(self) -> Optional[float]:
        return min(self.asks) if self.asks else None

    def mid(self) -> Optional[float]:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return (bb + ba) / 2.0

    def spread(self) -> Optional[float]:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return ba - bb

    def size_at(self, side: str, price: float) -> float:
        book = self.bids if side == "BUY" else self.asks
        return book.get(price, 0.0)


# --------------------------------------------------------------------------
# Paper orders / positions with conservative queue model
# --------------------------------------------------------------------------

@dataclass
class PaperOrder:
    token: str
    side: str                # "BUY" or "SELL" (our resting side)
    price: float
    size: float              # shares
    queue_ahead: float       # displayed shares ahead of us at placement
    placed_ts: float
    filled: float = 0.0
    purpose: str = "entry"   # "entry" or "tp"

    def remaining(self) -> float:
        return max(self.size - self.filled, 0.0)

    def on_trade(self, trade_price: float, trade_size: float, aggressor: str) -> float:
        """Feed a printed trade; return shares newly filled to us (conservative)."""
        if self.remaining() <= 0:
            return 0.0
        # Our BUY rests on the bid: it is hit by SELL aggressors at price <= ours.
        # Our SELL rests on the ask: lifted by BUY aggressors at price >= ours.
        if self.side == "BUY":
            relevant = aggressor == "SELL" and trade_price <= self.price + 1e-9
            through = trade_price < self.price - 1e-9
        else:
            relevant = aggressor == "BUY" and trade_price >= self.price - 1e-9
            through = trade_price > self.price + 1e-9
        if not relevant:
            return 0.0
        if through:
            fill = self.remaining()
            self.filled += fill
            return fill
        # Trade exactly at our price: burn queue first.
        if self.queue_ahead > 0:
            burn = min(self.queue_ahead, trade_size)
            self.queue_ahead -= burn
            trade_size -= burn
        if trade_size <= 0:
            return 0.0
        fill = min(self.remaining(), trade_size)
        self.filled += fill
        return fill


@dataclass
class Position:
    token: str
    market: str
    entry_price: float
    size: float
    opened_ts: float
    rebate_earned: float = 0.0


@dataclass
class TokenState:
    token: str
    market_slug: str
    outcome: str
    book: OrderBook = field(default_factory=OrderBook)
    entry_order: Optional[PaperOrder] = None
    tp_order: Optional[PaperOrder] = None
    position: Optional[Position] = None


# --------------------------------------------------------------------------
# Recorder — full L2 capture for offline replay
# --------------------------------------------------------------------------

class Recorder:
    def __init__(self, path: str) -> None:
        self._fh = gzip.open(path, "at", encoding="utf-8")
        self._n = 0

    def write(self, obj: dict) -> None:
        obj["_ts"] = round(time.time(), 3)
        self._fh.write(json.dumps(obj, separators=(",", ":")) + "\n")
        self._n += 1
        if self._n % 500 == 0:
            self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.flush()
            self._fh.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Trade log
# --------------------------------------------------------------------------

CSV_FIELDS = [
    "ts", "market", "token", "outcome", "event", "side", "price", "size",
    "fee", "rebate", "pnl", "reason", "hold_s",
]


class TradeLog:
    def __init__(self, path: str) -> None:
        new = not os.path.exists(path)
        self._fh = open(path, "a", newline="")
        self._w = csv.DictWriter(self._fh, fieldnames=CSV_FIELDS)
        if new:
            self._w.writeheader()
        self.realized = 0.0
        self.fills = 0
        self.closes = 0
        self.wins = 0

    def write(self, **row) -> None:
        base = {k: "" for k in CSV_FIELDS}
        base.update(row)
        base["ts"] = f"{time.time():.1f}"
        self._w.writerow(base)
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Quoting engine
# --------------------------------------------------------------------------

class Quoter:
    def __init__(self, trades: TradeLog) -> None:
        self.trades = trades
        self.total_inventory_usd = 0.0

    # ---- entry side ----

    def maybe_quote(self, st: TokenState) -> None:
        if st.position is not None or st.entry_order is not None:
            return
        book = st.book
        mid, spread = book.mid(), book.spread()
        if mid is None or spread is None:
            return
        if not (CFG.min_mid <= mid <= CFG.max_mid):
            return
        if spread < CFG.min_spread_ticks * book.tick - 1e-9:
            return
        if self.total_inventory_usd >= CFG.max_inv_total:
            return
        bb = book.best_bid()
        px = round(bb + book.tick, 4)
        if px >= book.best_ask() - 1e-9:  # would cross; join the bid instead
            px = bb
        size = CFG.quote_size_usd / px
        st.entry_order = PaperOrder(
            token=st.token, side="BUY", price=px, size=size,
            queue_ahead=book.size_at("BUY", px), placed_ts=time.time(),
        )
        log.info("QUOTE %s %s bid=%.3f size=%.2f queue_ahead=%.1f spread=%.3f",
                 st.market_slug, st.outcome, px, size, st.entry_order.queue_ahead, spread)

    def on_trade_print(self, st: TokenState, price: float, size: float, aggressor: str) -> None:
        now = time.time()
        if st.entry_order:
            filled = st.entry_order.on_trade(price, size, aggressor)
            if filled > 0:
                self._on_entry_fill(st, filled, now)
        if st.tp_order:
            filled = st.tp_order.on_trade(price, size, aggressor)
            if filled > 0:
                self._on_tp_fill(st, filled, now)

    def _on_entry_fill(self, st: TokenState, filled: float, now: float) -> None:
        o = st.entry_order
        rebate = maker_rebate(o.price, filled)
        if st.position is None:
            st.position = Position(st.token, st.market_slug, o.price, filled, now, rebate)
        else:
            p = st.position
            p.entry_price = (p.entry_price * p.size + o.price * filled) / (p.size + filled)
            p.size += filled
            p.rebate_earned += rebate
        self.total_inventory_usd += o.price * filled
        self.trades.fills += 1
        self.trades.write(event="ENTRY_FILL", market=st.market_slug, token=st.token,
                          outcome=st.outcome, side="BUY", price=f"{o.price:.4f}",
                          size=f"{filled:.4f}", rebate=f"{rebate:.5f}", reason="maker_fill")
        log.info("FILL entry %s %s px=%.3f size=%.2f rebate=%.4f",
                 st.market_slug, st.outcome, o.price, filled, rebate)
        if o.remaining() <= 1e-9:
            st.entry_order = None
        self._place_tp(st)

    def _place_tp(self, st: TokenState) -> None:
        if st.position is None or st.tp_order is not None:
            return
        book = st.book
        tp_px = round(st.position.entry_price + CFG.tp_ticks * book.tick, 4)
        st.tp_order = PaperOrder(
            token=st.token, side="SELL", price=tp_px, size=st.position.size,
            queue_ahead=book.size_at("SELL", tp_px), placed_ts=time.time(), purpose="tp",
        )
        log.info("QUOTE tp %s %s ask=%.3f size=%.2f queue_ahead=%.1f",
                 st.market_slug, st.outcome, tp_px, st.position.size, st.tp_order.queue_ahead)

    def _on_tp_fill(self, st: TokenState, filled: float, now: float) -> None:
        pos, o = st.position, st.tp_order
        rebate = maker_rebate(o.price, filled)
        pnl = (o.price - pos.entry_price) * filled + rebate + \
              (pos.rebate_earned * (filled / pos.size) if pos.size else 0.0)
        self._close(st, filled, o.price, pnl, fee=0.0, rebate=rebate,
                    reason="tp_maker", now=now)
        if o.remaining() <= 1e-9:
            st.tp_order = None

    # ---- risk exits (taker, fee-modeled) ----

    def check_risk(self, st: TokenState) -> None:
        now = time.time()
        book = st.book
        # cancel stale entry quote / reprice if market moved
        if st.entry_order and st.entry_order.purpose == "entry":
            o = st.entry_order
            bb = book.best_bid()
            stale = now - o.placed_ts > CFG.order_ttl
            off_market = bb is not None and (bb - o.price) > 2 * book.tick + 1e-9
            if stale or off_market:
                log.info("CANCEL entry quote %s %s px=%.3f reason=%s",
                         st.market_slug, st.outcome, o.price,
                         "ttl" if stale else "off_market")
                st.entry_order = None  # repricing next cycle resets queue (conservative)
        if st.position is None:
            return
        pos = st.position
        bb = book.best_bid()
        if bb is None:
            return
        sl_px = pos.entry_price - CFG.sl_ticks * book.tick
        timed_out = now - pos.opened_ts > CFG.position_ttl
        if bb <= sl_px + 1e-9 or timed_out:
            fee = taker_fee(bb, pos.size)
            pnl = (bb - pos.entry_price) * pos.size - fee + pos.rebate_earned
            self._close(st, pos.size, bb, pnl, fee=fee, rebate=0.0,
                        reason="sl_taker" if not timed_out else "ttl_taker", now=now)
            st.tp_order = None

    def _close(self, st: TokenState, size: float, px: float, pnl: float,
               fee: float, rebate: float, reason: str, now: float) -> None:
        pos = st.position
        hold = now - pos.opened_ts
        self.total_inventory_usd = max(0.0, self.total_inventory_usd - pos.entry_price * size)
        self.trades.realized += pnl
        self.trades.closes += 1
        if pnl > 0:
            self.trades.wins += 1
        self.trades.write(event="CLOSE", market=st.market_slug, token=st.token,
                          outcome=st.outcome, side="SELL", price=f"{px:.4f}",
                          size=f"{size:.4f}", fee=f"{fee:.5f}", rebate=f"{rebate:.5f}",
                          pnl=f"{pnl:.5f}", reason=reason, hold_s=f"{hold:.1f}")
        log.info("CLOSE %s %s px=%.3f pnl=%+.4f reason=%s hold=%.0fs",
                 st.market_slug, st.outcome, px, pnl, reason, hold)
        pos.size -= size
        if pos.size <= 1e-9:
            st.position = None


# --------------------------------------------------------------------------
# Discovery — live tennis match markets only
# --------------------------------------------------------------------------

def _parse_ts(s: str) -> Optional[float]:
    if not s:
        return None
    try:
        from datetime import datetime, timezone
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s).astimezone(timezone.utc).timestamp()
    except Exception:
        return None


def is_live_match_market(title: str, slug: str, game_start: Optional[float],
                         now: Optional[float] = None) -> bool:
    """Match markets only: needs a game start inside the window, futures excluded."""
    now = now or time.time()
    text = f"{title} {slug}"
    if FUTURES_RE.search(text):
        return False
    if game_start is None:
        return False
    return (now - CFG.lookback_h * 3600) <= game_start <= (now + CFG.lookahead_h * 3600)


def discover_tennis_tokens() -> Dict[str, Tuple[str, str]]:
    """Return {token_id: (market_slug, outcome)} for live tennis match markets."""
    out: Dict[str, Tuple[str, str]] = {}
    params_base = {"active": "true", "closed": "false", "limit": "100"}
    for extra in ({"tag_slug": "tennis"}, {}):
        try:
            r = requests.get(GAMMA_URL, params={**params_base, **extra}, timeout=15)
            r.raise_for_status()
            events = r.json()
        except Exception as e:
            log.warning("Gamma discovery failed (%s): %s", extra or "no tag", e)
            continue
        for ev in events if isinstance(events, list) else []:
            ev_title = (ev.get("title") or "")
            if extra == {} and "tennis" not in json.dumps(ev).lower():
                continue
            for m in ev.get("markets") or []:
                slug = m.get("slug") or ""
                title = m.get("question") or ev_title
                gs = _parse_ts(m.get("gameStartTime") or ev.get("startDate") or "")
                if not is_live_match_market(title, slug, gs):
                    continue
                try:
                    tokens = json.loads(m.get("clobTokenIds") or "[]")
                    outcomes = json.loads(m.get("outcomes") or "[]")
                except Exception:
                    continue
                for tok, oc in zip(tokens, outcomes):
                    out[str(tok)] = (slug, str(oc))
        if out:
            break
    return out


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

async def run() -> None:
    import websockets

    recorder = Recorder(CFG.l2_log)
    trades = TradeLog(CFG.trade_csv)
    quoter = Quoter(trades)
    states: Dict[str, TokenState] = {}
    start = time.time()
    stop = asyncio.Event()

    def _sig(*_a):
        stop.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(s, _sig)
        except NotImplementedError:
            pass

    async def dashboard():
        while not stop.is_set():
            await asyncio.sleep(CFG.dashboard_interval)
            quoting = sum(1 for s in states.values() if s.entry_order)
            open_pos = sum(1 for s in states.values() if s.position)
            log.info("DASH tokens=%d quoting=%d positions=%d fills=%d closes=%d "
                     "wins=%d realized=%+.4f inv=%.2f",
                     len(states), quoting, open_pos, trades.fills, trades.closes,
                     trades.wins, trades.realized, quoter.total_inventory_usd)

    async def risk_loop():
        while not stop.is_set():
            await asyncio.sleep(1.0)
            for st in list(states.values()):
                quoter.check_risk(st)
                quoter.maybe_quote(st)
            if time.time() - start > CFG.max_runtime:
                log.info("Maximum runtime reached")
                stop.set()

    async def ws_session(tokens: List[str]):
        async with websockets.connect(WS_URL, ping_interval=10, ping_timeout=20) as ws:
            await ws.send(json.dumps({"type": "market", "assets_ids": tokens}))
            log.info("WS subscribed to %d tokens", len(tokens))
            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                except asyncio.TimeoutError:
                    continue
                msgs = json.loads(raw)
                if isinstance(msgs, dict):
                    msgs = [msgs]
                for msg in msgs:
                    et = msg.get("event_type")
                    tok = str(msg.get("asset_id") or msg.get("assetId") or "")
                    recorder.write({"e": et, **msg})
                    st = states.get(tok)
                    if st is None:
                        continue
                    if et == "book":
                        st.book.apply_snapshot(msg.get("bids") or msg.get("buys") or [],
                                               msg.get("asks") or msg.get("sells") or [])
                    elif et == "price_change":
                        for ch in msg.get("changes") or []:
                            st.book.apply_change(float(ch["price"]), ch["side"],
                                                 float(ch["size"]))
                    elif et == "tick_size_change":
                        try:
                            st.book.tick = float(msg.get("new_tick_size"))
                        except (TypeError, ValueError):
                            pass
                    elif et == "last_trade_price":
                        try:
                            quoter.on_trade_print(st, float(msg["price"]),
                                                  float(msg.get("size") or 0),
                                                  str(msg.get("side") or "").upper())
                        except (KeyError, ValueError):
                            pass

    async def ws_manager():
        current: List[str] = []
        while not stop.is_set():
            found = await asyncio.to_thread(discover_tennis_tokens)
            for tok, (slug, oc) in found.items():
                if tok not in states:
                    states[tok] = TokenState(tok, slug, oc)
            tokens = sorted(states.keys())
            if not tokens:
                log.info("No live tennis match markets right now; retrying in %ss",
                         CFG.discovery_interval)
                await asyncio.sleep(CFG.discovery_interval)
                continue
            if tokens != current:
                current = tokens
                log.info("Discovery: %d live-match tokens across %d markets",
                         len(tokens), len({s.market_slug for s in states.values()}))
            try:
                await asyncio.wait_for(ws_session(current),
                                       timeout=CFG.discovery_interval)
            except asyncio.TimeoutError:
                pass  # periodic reconnect picks up new markets
            except Exception as e:
                log.warning("WS error: %s; reconnecting in 5s", e)
                await asyncio.sleep(5)

    tasks = [asyncio.create_task(c) for c in (dashboard(), risk_loop(), ws_manager())]
    await stop.wait()
    for t in tasks:
        t.cancel()
    recorder.close()
    trades.close()
    log.info("Session done: fills=%d closes=%d wins=%d realized=%+.4f",
             trades.fills, trades.closes, trades.wins, trades.realized)


# --------------------------------------------------------------------------
# Self-test (offline)
# --------------------------------------------------------------------------

def self_test() -> int:
    failures = []

    def check(name, cond):
        (failures.append(name) if not cond else None)
        print(("PASS " if cond else "FAIL ") + name)

    # fee / rebate math
    check("fee at p=0.5", abs(taker_fee(0.5, 10) - 0.05 * 0.25 * 10) < 1e-9)
    check("rebate < fee", maker_rebate(0.5, 10) < taker_fee(0.5, 10))
    check("fee -> 0 at extremes", taker_fee(0.99, 10) < taker_fee(0.5, 10))

    # book mechanics
    b = OrderBook()
    b.apply_snapshot([{"price": "0.40", "size": "100"}, {"price": "0.39", "size": "50"}],
                     [{"price": "0.44", "size": "80"}])
    check("best bid", b.best_bid() == 0.40)
    check("spread", abs(b.spread() - 0.04) < 1e-9)
    b.apply_change(0.40, "BUY", 0)
    check("level removal", b.best_bid() == 0.39)

    # queue model: conservative fills
    o = PaperOrder("t", "BUY", 0.41, 10, queue_ahead=30, placed_ts=0)
    check("wrong aggressor no fill", o.on_trade(0.41, 100, "BUY") == 0.0)
    check("queue burns first", o.on_trade(0.41, 20, "SELL") == 0.0 and o.queue_ahead == 10)
    check("partial after queue", abs(o.on_trade(0.41, 15, "SELL") - 5.0) < 1e-9)
    check("trade-through fills rest", abs(o.on_trade(0.40, 1, "SELL") - 5.0) < 1e-9)
    check("no overfill", o.on_trade(0.41, 100, "SELL") == 0.0)

    s = PaperOrder("t", "SELL", 0.60, 10, queue_ahead=0, placed_ts=0)
    check("sell filled by BUY aggr", abs(s.on_trade(0.60, 4, "BUY") - 4.0) < 1e-9)
    check("sell ignores SELL aggr", s.on_trade(0.60, 4, "SELL") == 0.0)

    # futures filter
    now = time.time()
    check("futures excluded",
          not is_live_match_market("Will Iga Swiatek win the 2026 Womens US Open?",
                                   "will-iga-swiatek-win-the-2026-womens-us-open",
                                   now, now))
    check("match with start ok",
          is_live_match_market("Alcaraz vs. Sinner", "alcaraz-sinner", now + 3600, now))
    check("no gametime excluded",
          not is_live_match_market("Alcaraz vs. Sinner", "alcaraz-sinner", None, now))
    check("stale match excluded",
          not is_live_match_market("Alcaraz vs. Sinner", "alcaraz-sinner",
                                   now - 40 * 3600, now))

    # economics sanity: maker round trip at min TP must be net positive
    entry, tick = 0.50, 0.01
    tp = entry + CFG.tp_ticks * tick
    shares = CFG.quote_size_usd / entry
    rt = (tp - entry) * shares + maker_rebate(entry, shares) + maker_rebate(tp, shares)
    check("maker TP round trip positive", rt > 0)
    sl = entry - CFG.sl_ticks * tick
    sl_pnl = (sl - entry) * shares - taker_fee(sl, shares) + maker_rebate(entry, shares)
    check("SL loss bounded (< quote size)", -sl_pnl < CFG.quote_size_usd)
    check("TP/SL asymmetry sane", CFG.sl_ticks >= CFG.tp_ticks)

    print(f"\n{len(failures)} failure(s)" if failures else "\nAll self-tests passed.")
    return 1 if failures else 0


# --------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(CFG.log_file)],
    )
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    if requests is None:
        print("requests not installed; run inside the bot venv")
        sys.exit(1)
    log.info("PAPER maker session starting (no live order path exists in this build)")
    asyncio.run(run())


if __name__ == "__main__":
    main()
