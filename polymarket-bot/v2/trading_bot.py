#!/usr/bin/env python3
"""Polymarket sports trading bot v2 — tennis, table tennis, MLB.

Design notes (read before touching):
  * Fees changed in 2026. Sports markets charge TAKER fees of
    fee = shares * rate * p * (1-p)  (rate ~0.05 as of Jul 2026).
    MAKER orders pay zero and earn rebates. A round-trip taker scalp near
    p=0.5 costs ~5% of notional before spread — the user's own backtests
    already falsified taker scalping even before these fees existed.
  * Therefore the strategy layer is MAKER-FIRST:
      - maker_scalp:   quote inside wide spreads, earn the spread.
      - fade_shock:    after a sharp drop, rest a maker bid at a discount
                       ("retail overreaction" fade). Up-spikes are covered
                       automatically because the complement token drops.
      - complement_arb: taker, but only when YES.ask + NO.ask + fees < $1
                       (locked profit at resolution).
    Taker orders are otherwise used only as stop-loss insurance and as a
    timeout fallback on exits.
  * DRY_RUN=true simulates fills locally. Live mode requires DRY_RUN=false
    AND LIVE_CONFIRM=I-ACCEPT-FULL-LOSS-RISK AND PRIVATE_KEY. Anything else
    refuses to trade real money.
  * strategy_params.json (produced by analyze_history.py) overrides
    per-sport parameters, closing the loop from historical study to live
    settings.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import math
import os
import signal
import statistics
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:  # optional at import time so --self-test runs anywhere
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

try:
    import websockets  # type: ignore
except Exception:  # pragma: no cover
    websockets = None

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CLOB_HOST = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def safe_float(v: Any, default: float | None = None) -> float | None:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_datetime(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        s = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_json_list(v: Any) -> list[Any]:
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            out = json.loads(v)
            return out if isinstance(out, list) else []
        except json.JSONDecodeError:
            return []
    return []


def truthy(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# sports
# ---------------------------------------------------------------------------
# Order matters: "table tennis" contains "tennis".
TABLE_TENNIS_KEYWORDS = (
    "table tennis", "table-tennis", "setka", "wtt ", "wtt-", "wtt:",
    "tt cup", "ttcup", "tt elite", "liga pro", "ping pong", "wttms", "wttws",
)
TENNIS_KEYWORDS = (
    "tennis", "atp", "wta", "itf", "challenger", "grand slam", "wimbledon",
    "roland garros", "us open", "australian open",
)
BASEBALL_KEYWORDS = (
    "mlb", "baseball", "american league", "national league", "world series",
)

SPORT_KEYS = ("table_tennis", "tennis", "mlb")
SPORT_LABEL = {"table_tennis": "TableTennis", "tennis": "Tennis", "mlb": "MLB"}


def classify_sport(text: str) -> str | None:
    t = text.lower()
    if any(k in t for k in TABLE_TENNIS_KEYWORDS):
        return "table_tennis"
    if any(k in t for k in TENNIS_KEYWORDS):
        return "tennis"
    if any(k in t for k in BASEBALL_KEYWORDS):
        return "mlb"
    return None


# ---------------------------------------------------------------------------
# per-sport strategy parameters (overridable via strategy_params.json)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SportParams:
    # sizing / risk
    risk_fraction: float = 0.05          # of sport bankroll per position
    per_trade_cap_usd: float = 50.0
    max_open_positions: int = 4
    # maker scalp
    scalp_enabled: bool = True
    min_spread_ticks: int = 3            # only quote when spread >= this
    improve_ticks: int = 1               # place bid at best_bid + improve*tick
    scalp_tp_ticks: int = 2              # resting maker exit above entry
    order_ttl_seconds: float = 45.0      # cancel unfilled entry quotes
    # overreaction fade
    fade_enabled: bool = True
    shock_window_seconds: float = 20.0
    shock_z: float = 3.0                 # |move| vs EWMA abs move
    shock_min_abs: float = 0.04          # and at least this many prob points
    retrace_fraction: float = 0.35       # target = post-shock mid + frac*shock
    fade_extra_discount_ticks: int = 1   # rest below best bid by this
    fade_tp_fraction: float = 0.5        # exit at recovery of this much shock
    fade_ttl_seconds: float = 30.0
    # complement arb
    arb_enabled: bool = True
    arb_min_edge: float = 0.004          # after fees, per $1 pair
    # exits / safety
    stop_loss_abs: float = 0.06          # taker insurance exit
    maker_exit_timeout_seconds: float = 240.0  # then cross the spread
    max_hold_seconds: float = 2400.0
    min_top_depth_usd: float = 5.0
    warmup_samples: int = 15
    price_floor: float = 0.05
    price_ceiling: float = 0.95
    cooldown_seconds: float = 45.0


DEFAULT_PARAMS: dict[str, SportParams] = {
    "tennis": SportParams(),
    "table_tennis": SportParams(
        min_spread_ticks=3, per_trade_cap_usd=25.0, max_open_positions=6,
        shock_z=2.5, min_top_depth_usd=3.0,
    ),
    "mlb": SportParams(
        min_spread_ticks=4, shock_z=3.5, max_hold_seconds=3600.0,
    ),
}


def load_params(path: Path) -> dict[str, SportParams]:
    params = dict(DEFAULT_PARAMS)
    if not path.exists():
        return params
    try:
        raw = json.loads(path.read_text())
    except Exception:
        logging.getLogger("bot").warning("could not parse %s; using defaults", path)
        return params
    for sport, overrides in raw.items():
        if sport in params and isinstance(overrides, dict):
            valid = {k: v for k, v in overrides.items() if hasattr(params[sport], k)}
            params[sport] = replace(params[sport], **valid)
    return params


# ---------------------------------------------------------------------------
# runtime config
# ---------------------------------------------------------------------------

def parse_allocations(spec: str) -> dict[str, float]:
    """"tennis=150,table_tennis=100,mlb=50" -> {...}. Sports absent = disabled."""
    out: dict[str, float] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, val = part.partition("=")
        key = key.strip().lower().replace("-", "_").replace(" ", "_")
        if key in ("tabletennis", "tt"):
            key = "table_tennis"
        if key in ("baseball",):
            key = "mlb"
        amount = safe_float(val, None)
        if key in SPORT_KEYS and amount is not None and amount > 0:
            out[key] = amount
    return out


@dataclass(frozen=True)
class RuntimeConfig:
    dry_run: bool = truthy(os.getenv("DRY_RUN", "true"), True)
    live_confirm: str = os.getenv("LIVE_CONFIRM", "")
    private_key: str = os.getenv("PRIVATE_KEY", "")
    funder: str = os.getenv("POLY_FUNDER", "")          # proxy wallet address, if any
    signature_type: int = int(os.getenv("POLY_SIGNATURE_TYPE", "0"))
    allocations: dict[str, float] = field(
        default_factory=lambda: parse_allocations(
            os.getenv("SPORT_ALLOCATIONS", "tennis=150,table_tennis=100,mlb=50")
        )
    )
    taker_fee_rate: float = float(os.getenv("TAKER_FEE_RATE", "0.05"))
    discovery_interval_seconds: float = float(os.getenv("DISCOVERY_INTERVAL_SECONDS", "600"))
    dashboard_interval_seconds: float = float(os.getenv("DASHBOARD_INTERVAL_SECONDS", "5"))
    heartbeat_seconds: float = 10.0
    stale_book_seconds: float = float(os.getenv("STALE_BOOK_SECONDS", "20"))
    ewma_alpha: float = float(os.getenv("EWMA_ALPHA", "0.18"))
    history_size: int = int(os.getenv("HISTORY_SIZE", "90"))
    sample_interval_seconds: float = float(os.getenv("SAMPLE_INTERVAL_SECONDS", "1"))
    discovery_page_size: int = 100
    max_discovery_events: int = int(os.getenv("MAX_DISCOVERY_EVENTS", "2000"))
    max_subscription_assets: int = int(os.getenv("MAX_SUBSCRIPTION_ASSETS", "1000"))
    daily_max_drawdown_pct: float = float(os.getenv("DAILY_MAX_DRAWDOWN_PCT", "10"))
    session_profit_lock_pct: float = float(os.getenv("SESSION_PROFIT_LOCK_PCT", "0"))
    kill_file: Path = Path(os.getenv("KILL_FILE", "STOP"))
    params_file: Path = Path(os.getenv("STRATEGY_PARAMS_FILE", "strategy_params.json"))
    trade_csv: Path = Path(os.getenv("TRADE_CSV", "trades.csv"))
    log_file: Path = Path(os.getenv("LOG_FILE", "sports_trading_bot.log"))
    clear_dashboard: bool = truthy(os.getenv("CLEAR_DASHBOARD", "true"), True)


def taker_fee(shares: float, price: float, rate: float) -> float:
    """Polymarket 2026 sports taker fee: shares * rate * p * (1-p). Makers pay 0."""
    p = clamp(price, 0.0, 1.0)
    return max(0.0, shares * rate * p * (1.0 - p))


# ---------------------------------------------------------------------------
# market / book state
# ---------------------------------------------------------------------------

@dataclass
class MarketMeta:
    token_id: str
    complement_token_id: str | None
    market_id: str
    event_slug: str
    market_slug: str
    question: str
    outcome: str | None
    sport: str
    tick_size: float
    game_start_time: datetime | None
    end_time: datetime | None

    def stage(self, now: datetime | None = None) -> str:
        now = now or utc_now()
        if self.end_time and now >= self.end_time:
            return "post"
        if self.game_start_time and now >= self.game_start_time:
            return "during"
        return "pre"


@dataclass
class BookState:
    tick_size: float = 0.01
    best_bid: float | None = None
    best_ask: float | None = None
    bid_size: float = 0.0
    ask_size: float = 0.0
    last_trade_price: float | None = None
    received_monotonic: float = 0.0
    history: deque = field(default_factory=lambda: deque(maxlen=600))  # (mono, mid)
    ewma_abs_move: float | None = None
    _last_sample_mono: float = 0.0
    _last_sample_mid: float | None = None
    last_reason: str = "INIT"

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def sample(self, now_mono: float, interval: float, alpha: float) -> None:
        mid = self.mid
        if mid is None:
            return
        if now_mono - self._last_sample_mono < interval:
            return
        if self._last_sample_mid is not None:
            move = abs(mid - self._last_sample_mid)
            self.ewma_abs_move = (
                move if self.ewma_abs_move is None
                else alpha * move + (1 - alpha) * self.ewma_abs_move
            )
        self.history.append((now_mono, mid))
        self._last_sample_mono = now_mono
        self._last_sample_mid = mid

    def mid_at(self, seconds_ago: float) -> float | None:
        """Mid price closest to `seconds_ago` in the sampled history."""
        if not self.history:
            return None
        target = self.history[-1][0] - seconds_ago
        best = None
        for mono, mid in reversed(self.history):
            if mono <= target:
                best = mid
                break
            best = mid
        return best


# ---------------------------------------------------------------------------
# orders / positions / accounting
# ---------------------------------------------------------------------------

@dataclass
class RestingOrder:
    order_id: str
    token_id: str
    side: str                 # BUY / SELL
    price: float
    size: float               # shares
    strategy: str             # scalp / fade / exit
    placed_mono: float
    ttl: float
    purpose: str = "entry"    # entry / exit
    linked_position: str | None = None
    live_order_id: str | None = None


@dataclass
class Position:
    token_id: str
    sport: str
    slug: str
    outcome: str | None
    strategy: str
    entry_price: float
    quantity: float
    entry_fee: float
    opened_at: datetime
    is_maker_entry: bool
    take_profit_price: float
    stop_loss_price: float
    max_hold_seconds: float
    mark: float | None = None
    exit_order_id: str | None = None
    arb_locked_pnl: float | None = None   # complement-arb pairs: locked at entry


@dataclass
class ClosedTrade:
    closed_at: datetime
    sport: str
    slug: str
    outcome: str | None
    strategy: str
    side_entry: str
    entry_price: float
    exit_price: float
    quantity: float
    fees: float
    pnl: float
    reason: str
    hold_seconds: float


class Portfolio:
    def __init__(self, allocations: dict[str, float]) -> None:
        self.allocations = dict(allocations)
        self.bankrolls = dict(allocations)
        self.realized: dict[str, float] = {s: 0.0 for s in allocations}
        self.fees_paid: dict[str, float] = {s: 0.0 for s in allocations}
        self.positions: dict[str, Position] = {}
        self.closed: list[ClosedTrade] = []
        self.last_trade_at: dict[str, float] = {}
        self.session_start = utc_now()
        self.equity_high = sum(allocations.values())

    def sport_open_count(self, sport: str) -> int:
        return sum(1 for p in self.positions.values() if p.sport == sport)

    def market_exposed(self, meta: MarketMeta) -> bool:
        ids = {meta.token_id, meta.complement_token_id}
        return any(p.token_id in ids for p in self.positions.values())

    def unrealized(self) -> dict[str, float]:
        out = {s: 0.0 for s in self.allocations}
        for p in self.positions.values():
            if p.arb_locked_pnl is not None:
                out[p.sport] += p.arb_locked_pnl
            elif p.mark is not None:
                out[p.sport] += (p.mark - p.entry_price) * p.quantity
        return out

    def equity(self) -> dict[str, float]:
        unreal = self.unrealized()
        return {s: self.bankrolls[s] +
                sum((p.mark or p.entry_price) * p.quantity
                    for p in self.positions.values()
                    if p.sport == s and p.arb_locked_pnl is None) +
                sum(p.entry_price * p.quantity + (p.arb_locked_pnl or 0.0)
                    for p in self.positions.values()
                    if p.sport == s and p.arb_locked_pnl is not None)
                for s in self.allocations} | {"_unreal": sum(unreal.values())}

    def total_equity(self) -> float:
        eq = self.equity()
        return sum(v for k, v in eq.items() if not k.startswith("_"))

    def drawdown_pct(self) -> float:
        total = self.total_equity()
        self.equity_high = max(self.equity_high, total)
        if self.equity_high <= 0:
            return 0.0
        return 100.0 * (self.equity_high - total) / self.equity_high

    def open(self, meta: MarketMeta, price: float, notional: float, fee: float,
             strategy: str, maker: bool, tp: float, sl: float,
             max_hold: float, arb_locked: float | None = None) -> Position | None:
        qty = notional / price if price > 0 else 0.0
        cost = notional + fee
        if qty <= 0 or cost > self.bankrolls.get(meta.sport, 0.0):
            return None
        self.bankrolls[meta.sport] -= cost
        self.fees_paid[meta.sport] += fee
        pos = Position(
            token_id=meta.token_id, sport=meta.sport, slug=meta.market_slug,
            outcome=meta.outcome, strategy=strategy, entry_price=price,
            quantity=qty, entry_fee=fee, opened_at=utc_now(),
            is_maker_entry=maker, take_profit_price=tp, stop_loss_price=sl,
            max_hold_seconds=max_hold, mark=price, arb_locked_pnl=arb_locked,
        )
        self.positions[meta.token_id] = pos
        self.last_trade_at[meta.token_id] = time.monotonic()
        return pos

    def close(self, token_id: str, exit_price: float, exit_fee: float,
              reason: str) -> ClosedTrade | None:
        pos = self.positions.pop(token_id, None)
        if not pos:
            return None
        proceeds = exit_price * pos.quantity - exit_fee
        self.bankrolls[pos.sport] += proceeds
        self.fees_paid[pos.sport] += exit_fee
        pnl = proceeds - pos.entry_price * pos.quantity - pos.entry_fee
        self.realized[pos.sport] += pnl
        trade = ClosedTrade(
            closed_at=utc_now(), sport=pos.sport, slug=pos.slug,
            outcome=pos.outcome, strategy=pos.strategy, side_entry="BUY",
            entry_price=pos.entry_price, exit_price=exit_price,
            quantity=pos.quantity, fees=pos.entry_fee + exit_fee, pnl=pnl,
            reason=reason,
            hold_seconds=(utc_now() - pos.opened_at).total_seconds(),
        )
        self.closed.append(trade)
        self.last_trade_at[token_id] = time.monotonic()
        return trade


class TradeRecorder:
    HEADER = ["closed_at", "sport", "slug", "outcome", "strategy", "entry_price",
              "exit_price", "quantity", "fees", "pnl", "reason", "hold_seconds"]

    def __init__(self, path: Path) -> None:
        self.path = path
        if not path.exists():
            with path.open("w", newline="") as fh:
                csv.writer(fh).writerow(self.HEADER)

    def append(self, t: ClosedTrade) -> None:
        with self.path.open("a", newline="") as fh:
            csv.writer(fh).writerow([
                t.closed_at.isoformat(), t.sport, t.slug, t.outcome, t.strategy,
                f"{t.entry_price:.4f}", f"{t.exit_price:.4f}", f"{t.quantity:.4f}",
                f"{t.fees:.5f}", f"{t.pnl:.5f}", t.reason, f"{t.hold_seconds:.1f}",
            ])


# ---------------------------------------------------------------------------
# executors
# ---------------------------------------------------------------------------

class PaperExecutor:
    """Simulates maker fills conservatively from BBO + trade prints.

    A resting BUY fills when the best ask crosses down to <= our price, or a
    trade prints at <= our price. This UNDERSTATES queue-position issues and
    OVERSTATES fill certainty at the touch — treat paper maker fills as an
    upper bound and hold to the go-live gate.
    """

    live = False

    def __init__(self) -> None:
        self._n = 0

    def place(self, order: RestingOrder) -> str:
        self._n += 1
        return f"paper-{self._n}"

    def cancel(self, order: RestingOrder) -> None:
        return

    def taker_buy(self, token_id: str, price: float, size: float) -> float:
        return price

    def taker_sell(self, token_id: str, price: float, size: float) -> float:
        return price


class LiveExecutor:
    """Real orders via py-clob-client. Constructed only after gates pass."""

    live = True

    def __init__(self, cfg: RuntimeConfig, log: logging.Logger) -> None:
        from py_clob_client.client import ClobClient  # lazy import
        from py_clob_client.clob_types import OrderArgs, OrderType, MarketOrderArgs
        from py_clob_client.order_builder.constants import BUY, SELL

        self._OrderArgs, self._OrderType = OrderArgs, OrderType
        self._MarketOrderArgs = MarketOrderArgs
        self._BUY, self._SELL = BUY, SELL
        kwargs: dict[str, Any] = {"key": cfg.private_key, "chain_id": POLYGON_CHAIN_ID}
        if cfg.funder:
            kwargs["funder"] = cfg.funder
            kwargs["signature_type"] = cfg.signature_type
        self.client = ClobClient(CLOB_HOST, **kwargs)
        self.client.set_api_creds(self.client.create_or_derive_api_creds())
        self.log = log
        self.log.warning("LIVE EXECUTOR ARMED — real orders will be sent")

    def place(self, order: RestingOrder) -> str:
        args = self._OrderArgs(
            price=round(order.price, 4), size=round(order.size, 2),
            side=self._BUY if order.side == "BUY" else self._SELL,
            token_id=order.token_id,
        )
        signed = self.client.create_order(args)
        resp = self.client.post_order(signed, self._OrderType.GTC)
        oid = str((resp or {}).get("orderID") or (resp or {}).get("orderId") or "")
        if not oid:
            raise RuntimeError(f"order rejected: {resp}")
        return oid

    def cancel(self, order: RestingOrder) -> None:
        if order.live_order_id:
            try:
                self.client.cancel(order.live_order_id)
            except Exception as exc:  # pragma: no cover
                self.log.warning("cancel failed %s: %s", order.live_order_id, exc)

    def _marketable(self, token_id: str, side: str, price: float, size: float) -> float:
        args = self._OrderArgs(price=round(price, 4), size=round(size, 2),
                               side=side, token_id=token_id)
        signed = self.client.create_order(args)
        otype = getattr(self._OrderType, "FAK", None) or self._OrderType.FOK
        self.client.post_order(signed, otype)
        return price

    def taker_buy(self, token_id: str, price: float, size: float) -> float:
        return self._marketable(token_id, self._BUY, min(0.999, price + 0.02), size)

    def taker_sell(self, token_id: str, price: float, size: float) -> float:
        return self._marketable(token_id, self._SELL, max(0.001, price - 0.02), size)


# ---------------------------------------------------------------------------
# the bot
# ---------------------------------------------------------------------------

class SportsTradingBot:
    def __init__(self, cfg: RuntimeConfig | None = None) -> None:
        self.cfg = cfg or RuntimeConfig()
        self.params = load_params(self.cfg.params_file)
        self.log = logging.getLogger("bot")
        self.registry: dict[str, MarketMeta] = {}
        self.books: dict[str, BookState] = {}
        self.by_market: dict[str, list[str]] = {}
        self.portfolio = Portfolio(self.cfg.allocations)
        self.recorder = TradeRecorder(self.cfg.trade_csv)
        self.orders: dict[str, RestingOrder] = {}
        self.stop_event = asyncio.Event()
        self.registry_changed = asyncio.Event()
        self.halted: str | None = None
        self.messages = 0
        self.last_discovery: datetime | None = None
        self.executor: PaperExecutor | LiveExecutor = PaperExecutor()

    # -- live gating --------------------------------------------------------
    def arm_executor(self) -> None:
        c = self.cfg
        if c.dry_run:
            self.log.info("DRY_RUN=true — paper trading only")
            return
        problems = []
        if c.live_confirm != "I-ACCEPT-FULL-LOSS-RISK":
            problems.append('LIVE_CONFIRM must be exactly "I-ACCEPT-FULL-LOSS-RISK"')
        if not c.private_key:
            problems.append("PRIVATE_KEY is not set")
        if problems:
            raise SystemExit("Refusing live mode:\n  - " + "\n  - ".join(problems))
        self.executor = LiveExecutor(c, self.log)

    # -- discovery ----------------------------------------------------------
    async def fetch_json(self, url: str, params: dict[str, Any]) -> Any:
        if requests is None:
            raise RuntimeError("requests not installed")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: requests.get(url, params=params, timeout=15).json()
        )

    async def discover_markets(self) -> dict[str, MarketMeta]:
        events: list[dict[str, Any]] = []
        offset = 0
        while offset < self.cfg.max_discovery_events:
            page = await self.fetch_json(GAMMA_EVENTS_URL, {
                "active": "true", "closed": "false", "order": "volume24hr",
                "ascending": "false", "limit": self.cfg.discovery_page_size,
                "offset": offset,
            })
            if not isinstance(page, list):
                break
            events.extend(e for e in page if isinstance(e, dict))
            if len(page) < self.cfg.discovery_page_size:
                break
            offset += self.cfg.discovery_page_size

        registry: dict[str, MarketMeta] = {}
        for event in events:
            etext = " ".join(str(event.get(k, "")) for k in
                             ("title", "description", "slug", "ticker", "tags"))
            e_sport = classify_sport(etext)
            e_start = parse_datetime(event.get("startDate"))
            e_end = parse_datetime(event.get("endDate"))
            e_slug = str(event.get("slug") or "unknown-event")
            for market in event.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                if market.get("closed") is True or market.get("active") is False:
                    continue
                if "enableOrderBook" in market and not truthy(market.get("enableOrderBook"), True):
                    continue
                question = str(market.get("question") or event.get("title") or "")
                sport = classify_sport(" ".join([etext, question,
                                                 str(market.get("slug", ""))])) or e_sport
                if sport not in self.cfg.allocations:
                    continue
                tokens = [str(t) for t in parse_json_list(
                    market.get("clobTokenIds") or market.get("clob_token_ids"))]
                outcomes = parse_json_list(market.get("outcomes"))
                if not tokens:
                    continue
                market_id = str(market.get("conditionId") or market.get("id") or e_slug)
                tick = clamp(safe_float(market.get("orderPriceMinTickSize"), 0.01) or 0.01,
                             0.001, 0.1)
                gstart = parse_datetime(market.get("gameStartTime")) or e_start
                gend = parse_datetime(market.get("endDate")) or e_end
                for i, tok in enumerate(tokens):
                    comp = tokens[1 - i] if len(tokens) == 2 else None
                    registry[tok] = MarketMeta(
                        token_id=tok, complement_token_id=comp, market_id=market_id,
                        event_slug=e_slug, market_slug=str(market.get("slug") or market_id),
                        question=question,
                        outcome=str(outcomes[i]) if i < len(outcomes) else None,
                        sport=sport, tick_size=tick,
                        game_start_time=gstart, end_time=gend,
                    )
        if len(registry) > self.cfg.max_subscription_assets:
            registry = dict(list(registry.items())[: self.cfg.max_subscription_assets])
        return registry

    async def refresh_registry(self) -> None:
        new = await self.discover_markets()
        changed = set(new) != set(self.registry)
        self.registry = new
        self.by_market = {}
        for tok, meta in new.items():
            self.books.setdefault(tok, BookState(tick_size=meta.tick_size))
            self.books[tok].tick_size = meta.tick_size
            self.by_market.setdefault(meta.market_id, []).append(tok)
        for gone in set(self.books) - set(new):
            if gone not in self.portfolio.positions:
                self.books.pop(gone, None)
        self.last_discovery = utc_now()
        counts = Counter(m.sport for m in new.values())
        self.log.info("discovered %d assets (%s)", len(new),
                      ", ".join(f"{SPORT_LABEL[s]}={counts.get(s, 0)}"
                                for s in self.cfg.allocations))
        if changed:
            self.registry_changed.set()

    async def discovery_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.refresh_registry()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.log.exception("discovery failed")
            try:
                await asyncio.wait_for(self.stop_event.wait(),
                                       timeout=self.cfg.discovery_interval_seconds)
            except asyncio.TimeoutError:
                pass

    # -- websocket ----------------------------------------------------------
    async def heartbeat(self, ws: Any) -> None:
        while True:
            await asyncio.sleep(self.cfg.heartbeat_seconds)
            await ws.send("PING")

    async def websocket_supervisor(self) -> None:
        if websockets is None:
            raise RuntimeError("websockets not installed")
        backoff = 1.0
        while not self.stop_event.is_set():
            asset_ids = list(self.registry)
            if not asset_ids:
                await asyncio.sleep(2.0)
                continue
            self.registry_changed.clear()
            try:
                async with websockets.connect(MARKET_WS_URL, ping_interval=None,
                                              open_timeout=15, close_timeout=5,
                                              max_queue=4096) as ws:
                    await ws.send(json.dumps({"assets_ids": asset_ids, "type": "market"}))
                    self.log.info("subscribed to %d assets", len(asset_ids))
                    backoff = 1.0
                    hb = asyncio.create_task(self.heartbeat(ws))
                    try:
                        while not self.stop_event.is_set() and not self.registry_changed.is_set():
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                            except asyncio.TimeoutError:
                                continue
                            if raw in ("PONG", b"PONG"):
                                continue
                            payload = json.loads(raw)
                            msgs = payload if isinstance(payload, list) else [payload]
                            for m in msgs:
                                if isinstance(m, dict):
                                    self.handle_message(m)
                    finally:
                        hb.cancel()
                        await asyncio.gather(hb, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log.warning("websocket dropped: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2.0)

    @staticmethod
    def _best_level(levels: Iterable[Any], highest: bool) -> tuple[float, float] | None:
        parsed = []
        for lv in levels:
            if isinstance(lv, dict):
                p, s = safe_float(lv.get("price")), safe_float(lv.get("size"), 0.0)
                if p is not None and s and s > 0:
                    parsed.append((p, s))
        if not parsed:
            return None
        return (max if highest else min)(parsed, key=lambda x: x[0])

    def handle_message(self, m: dict[str, Any]) -> None:
        self.messages += 1
        et = str(m.get("event_type") or "")
        if et == "book":
            tok = str(m.get("asset_id") or "")
            bb = self._best_level(m.get("bids") or [], True)
            ba = self._best_level(m.get("asks") or [], False)
            self.on_bbo(tok, bb[0] if bb else None, ba[0] if ba else None,
                        bb[1] if bb else 0.0, ba[1] if ba else 0.0)
        elif et == "price_change":
            for ch in m.get("price_changes") or []:
                if isinstance(ch, dict):
                    self.on_bbo(str(ch.get("asset_id") or ""),
                                safe_float(ch.get("best_bid")),
                                safe_float(ch.get("best_ask")), None, None)
        elif et == "best_bid_ask":
            self.on_bbo(str(m.get("asset_id") or ""), safe_float(m.get("best_bid")),
                        safe_float(m.get("best_ask")), None, None)
        elif et == "last_trade_price":
            tok = str(m.get("asset_id") or "")
            price = safe_float(m.get("price"))
            st = self.books.get(tok)
            if st is not None and price is not None:
                st.last_trade_price = price
                self.check_paper_fills(tok, trade_price=price)
        elif et == "tick_size_change":
            tok = str(m.get("asset_id") or "")
            nt = safe_float(m.get("new_tick_size"))
            if tok in self.books and nt:
                self.books[tok].tick_size = nt
        elif et == "market_resolved":
            for tok in m.get("assets_ids") or []:
                self.on_resolved(str(tok))

    # -- core tick ----------------------------------------------------------
    def on_bbo(self, token_id: str, bid: float | None, ask: float | None,
               bid_sz: float | None, ask_sz: float | None) -> None:
        meta, st = self.registry.get(token_id), self.books.get(token_id)
        if not meta or not st:
            return
        if bid is not None:
            st.best_bid = bid
        if ask is not None:
            st.best_ask = ask
        if bid_sz is not None:
            st.bid_size = bid_sz
        if ask_sz is not None:
            st.ask_size = ask_sz
        st.received_monotonic = time.monotonic()
        st.sample(st.received_monotonic, self.cfg.sample_interval_seconds,
                  self.cfg.ewma_alpha)

        self.check_paper_fills(token_id)
        self.expire_orders()

        pos = self.portfolio.positions.get(token_id)
        if pos and st.best_bid is not None and pos.arb_locked_pnl is None:
            pos.mark = st.best_bid

        if pos:
            self.evaluate_exit(meta, st, pos)
        elif not self.halted:
            self.evaluate_entry(meta, st)

    # -- paper maker fill model --------------------------------------------
    def check_paper_fills(self, token_id: str, trade_price: float | None = None) -> None:
        if self.executor.live:
            return  # live fills come from the exchange; polled elsewhere (v2.1)
        st = self.books.get(token_id)
        if not st:
            return
        for oid in [o for o, ordr in self.orders.items() if ordr.token_id == token_id]:
            order = self.orders.get(oid)
            if not order:
                continue
            hit = False
            if order.side == "BUY":
                if trade_price is not None and trade_price <= order.price + 1e-9:
                    hit = True
                elif st.best_ask is not None and st.best_ask <= order.price + 1e-9:
                    hit = True
            else:
                if trade_price is not None and trade_price >= order.price - 1e-9:
                    hit = True
                elif st.best_bid is not None and st.best_bid >= order.price - 1e-9:
                    hit = True
            if hit:
                self.on_fill(order)

    def on_fill(self, order: RestingOrder) -> None:
        self.orders.pop(order.order_id, None)
        meta = self.registry.get(order.token_id)
        if not meta:
            return
        p = self.params[meta.sport]
        if order.purpose == "entry" and order.side == "BUY":
            notional = order.price * order.size
            pos = self.portfolio.open(
                meta, order.price, notional, fee=0.0, strategy=order.strategy,
                maker=True,
                tp=(order.price + p.scalp_tp_ticks * meta.tick_size
                    if order.strategy == "scalp"
                    else order.price + p.fade_tp_fraction * abs(
                        safe_float(order.linked_position, 0.0) or 0.0)),
                sl=max(0.001, order.price - p.stop_loss_abs),
                max_hold=p.max_hold_seconds,
            )
            if pos:
                self.log.info("FILL %s BUY %s %s qty=%.2f @%.3f",
                              order.strategy.upper(), meta.sport, meta.market_slug,
                              order.size, order.price)
                self.place_maker_exit(meta, pos)
        elif order.purpose == "exit" and order.side == "SELL":
            trade = self.portfolio.close(order.token_id, order.price, 0.0,
                                         f"{order.strategy.upper()}_MAKER_TP")
            if trade:
                self.recorder.append(trade)
                self.log.info("EXIT maker %s %s pnl=%+.4f", trade.sport, trade.slug,
                              trade.pnl)

    def place_maker_exit(self, meta: MarketMeta, pos: Position) -> None:
        if meta.token_id not in self.portfolio.positions:
            return
        price = clamp(pos.take_profit_price, meta.tick_size, 1 - meta.tick_size)
        order = RestingOrder(order_id="", token_id=meta.token_id, side="SELL",
                             price=price, size=pos.quantity, strategy=pos.strategy,
                             placed_mono=time.monotonic(), ttl=1e9, purpose="exit")
        try:
            oid = self.executor.place(order)
        except Exception as exc:
            self.log.error("exit order failed: %s", exc)
            return
        order.order_id = oid
        order.live_order_id = oid if self.executor.live else None
        self.orders[oid] = order
        pos.exit_order_id = oid

    def cancel_order(self, oid: str) -> None:
        order = self.orders.pop(oid, None)
        if order:
            self.executor.cancel(order)

    def expire_orders(self) -> None:
        now = time.monotonic()
        for oid, order in list(self.orders.items()):
            if order.purpose == "entry" and now - order.placed_mono > order.ttl:
                self.cancel_order(oid)

    # -- entries ------------------------------------------------------------
    def evaluate_entry(self, meta: MarketMeta, st: BookState) -> None:
        p = self.params[meta.sport]
        if meta.stage() == "post":
            st.last_reason = "POST"
            return
        if st.best_bid is None or st.best_ask is None or st.best_bid >= st.best_ask:
            st.last_reason = "NO_BOOK"
            return
        if time.monotonic() - st.received_monotonic > self.cfg.stale_book_seconds:
            st.last_reason = "STALE"
            return
        mid = st.mid or 0.5
        if mid < p.price_floor or mid > p.price_ceiling:
            st.last_reason = "EXTREME_PRICE"
            return
        if self.portfolio.market_exposed(meta):
            st.last_reason = "MARKET_EXPOSED"
            return
        if any(o.token_id == meta.token_id and o.purpose == "entry"
               for o in self.orders.values()):
            st.last_reason = "ORDER_RESTING"
            return
        if time.monotonic() - self.portfolio.last_trade_at.get(meta.token_id, 0.0) \
                < p.cooldown_seconds:
            st.last_reason = "COOLDOWN"
            return
        if self.portfolio.sport_open_count(meta.sport) >= p.max_open_positions:
            st.last_reason = "MAX_POSITIONS"
            return
        if min(st.best_bid * st.bid_size, st.best_ask * st.ask_size) < p.min_top_depth_usd \
                and st.bid_size and st.ask_size:
            st.last_reason = "SHALLOW"
            return
        if len(st.history) < p.warmup_samples:
            st.last_reason = "WARMUP"
            return

        if p.arb_enabled and self.try_complement_arb(meta, st):
            return
        if p.fade_enabled and self.try_fade(meta, st):
            return
        if p.scalp_enabled and self.try_scalp(meta, st):
            return
        st.last_reason = "NO_SIGNAL"

    def _entry_size(self, meta: MarketMeta, price: float) -> float:
        p = self.params[meta.sport]
        bankroll = self.portfolio.bankrolls.get(meta.sport, 0.0)
        notional = min(p.per_trade_cap_usd, bankroll * p.risk_fraction)
        if notional < 1.0 or price <= 0:
            return 0.0
        return notional / price

    def _rest_buy(self, meta: MarketMeta, price: float, size: float,
                  strategy: str, ttl: float, shock: float | None = None) -> bool:
        order = RestingOrder(order_id="", token_id=meta.token_id, side="BUY",
                             price=round(price, 4), size=round(size, 2),
                             strategy=strategy, placed_mono=time.monotonic(),
                             ttl=ttl, purpose="entry",
                             linked_position=None if shock is None else f"{shock:.4f}")
        try:
            oid = self.executor.place(order)
        except Exception as exc:
            self.log.error("place failed: %s", exc)
            return False
        order.order_id = oid
        order.live_order_id = oid if self.executor.live else None
        self.orders[oid] = order
        self.log.info("QUOTE %s BUY %s %s %.2f@%.3f ttl=%.0fs", strategy.upper(),
                      meta.sport, meta.market_slug, size, price, ttl)
        return True

    def try_scalp(self, meta: MarketMeta, st: BookState) -> bool:
        p = self.params[meta.sport]
        spread_ticks = (st.spread or 0.0) / meta.tick_size
        if spread_ticks + 1e-9 < p.min_spread_ticks:
            st.last_reason = "SPREAD_TIGHT"
            return False
        price = st.best_bid + p.improve_ticks * meta.tick_size
        if price >= st.best_ask - meta.tick_size:      # keep at least 1 tick edge
            price = st.best_bid
        size = self._entry_size(meta, price)
        if size <= 0:
            st.last_reason = "NO_BANKROLL"
            return False
        return self._rest_buy(meta, price, size, "scalp", p.order_ttl_seconds)

    def try_fade(self, meta: MarketMeta, st: BookState) -> bool:
        p = self.params[meta.sport]
        past = st.mid_at(p.shock_window_seconds)
        mid = st.mid
        if past is None or mid is None:
            return False
        move = mid - past                      # negative = crash
        sigma = st.ewma_abs_move or 0.0
        if move >= 0 or sigma <= 0:
            return False
        if abs(move) < max(p.shock_min_abs, p.shock_z * sigma):
            return False
        target = mid - p.fade_extra_discount_ticks * meta.tick_size
        target = min(target, st.best_bid)      # never cross; stay maker
        target = clamp(target, meta.tick_size, 1 - meta.tick_size)
        size = self._entry_size(meta, target)
        if size <= 0:
            return False
        self.log.info("SHOCK %s %s move=%+.3f sigma=%.4f -> fade bid",
                      meta.sport, meta.market_slug, move, sigma)
        return self._rest_buy(meta, target, size, "fade", p.fade_ttl_seconds,
                              shock=abs(move))

    def try_complement_arb(self, meta: MarketMeta, st: BookState) -> bool:
        comp_id = meta.complement_token_id
        if not comp_id:
            return False
        comp = self.books.get(comp_id)
        if not comp or comp.best_ask is None or st.best_ask is None:
            return False
        rate = self.cfg.taker_fee_rate
        pair_cost = st.best_ask + comp.best_ask
        fees_per_pair = (taker_fee(1, st.best_ask, rate) +
                         taker_fee(1, comp.best_ask, rate))
        edge = 1.0 - pair_cost - fees_per_pair
        p = self.params[meta.sport]
        if edge < p.arb_min_edge:
            return False
        max_pairs = min(st.ask_size, comp.ask_size)
        budget_pairs = self._entry_size(meta, pair_cost) if pair_cost > 0 else 0.0
        pairs = math.floor(min(max_pairs, budget_pairs))
        if pairs < 1:
            return False
        fee_a = taker_fee(pairs, st.best_ask, rate)
        fee_b = taker_fee(pairs, comp.best_ask, rate)
        fill_a = self.executor.taker_buy(meta.token_id, st.best_ask, pairs)
        fill_b = self.executor.taker_buy(comp_id, comp.best_ask, pairs)
        locked = pairs * (1.0 - fill_a - fill_b) - fee_a - fee_b
        self.portfolio.open(meta, fill_a, fill_a * pairs, fee_a, "arb", False,
                            tp=1.0, sl=0.0, max_hold=1e9, arb_locked=locked / 2)
        comp_meta = self.registry.get(comp_id)
        if comp_meta:
            self.portfolio.open(comp_meta, fill_b, fill_b * pairs, fee_b, "arb",
                                False, tp=1.0, sl=0.0, max_hold=1e9,
                                arb_locked=locked / 2)
        self.log.info("ARB %s %s pairs=%d cost=%.4f locked=%.4f",
                      meta.sport, meta.market_slug, pairs, pair_cost, locked)
        st.last_reason = "ARB_TAKEN"
        return True

    # -- exits --------------------------------------------------------------
    def evaluate_exit(self, meta: MarketMeta, st: BookState, pos: Position) -> None:
        if pos.arb_locked_pnl is not None:
            return  # arb pairs settle at resolution
        if st.best_bid is None:
            return
        p = self.params[meta.sport]
        age = (utc_now() - pos.opened_at).total_seconds()
        rate = self.cfg.taker_fee_rate

        def taker_out(reason: str) -> None:
            if pos.exit_order_id:
                self.cancel_order(pos.exit_order_id)
            fee = taker_fee(pos.quantity, st.best_bid, rate)
            fill = self.executor.taker_sell(meta.token_id, st.best_bid, pos.quantity)
            trade = self.portfolio.close(meta.token_id, fill, fee, reason)
            if trade:
                self.recorder.append(trade)
                self.log.info("EXIT taker %s %s pnl=%+.4f fee=%.4f reason=%s",
                              trade.sport, trade.slug, trade.pnl, fee, reason)

        if st.best_bid <= pos.stop_loss_price:
            taker_out("STOP_LOSS")
        elif age >= pos.max_hold_seconds:
            taker_out("MAX_HOLD")
        elif meta.stage() == "post":
            taker_out("POST_STAGE")
        elif (pos.exit_order_id is None or pos.exit_order_id not in self.orders):
            self.place_maker_exit(meta, pos)
        elif age >= p.maker_exit_timeout_seconds and \
                st.best_bid >= pos.entry_price + meta.tick_size:
            # in profit but maker exit not filling — take what's there
            taker_out("TP_TIMEOUT")

    def on_resolved(self, token_id: str) -> None:
        pos = self.portfolio.positions.get(token_id)
        st = self.books.get(token_id)
        if not pos:
            return
        if pos.arb_locked_pnl is not None:
            trade = self.portfolio.close(
                token_id, pos.entry_price + pos.arb_locked_pnl / max(pos.quantity, 1e-9),
                0.0, "ARB_RESOLVED")
        else:
            price = st.best_bid if st and st.best_bid is not None else pos.entry_price
            trade = self.portfolio.close(token_id, price, 0.0, "RESOLVED")
        if trade:
            self.recorder.append(trade)

    # -- risk halts ---------------------------------------------------------
    def check_halts(self) -> None:
        dd = self.portfolio.drawdown_pct()
        if self.cfg.kill_file.exists():
            self.halt("KILL_FILE")
        elif dd >= self.cfg.daily_max_drawdown_pct:
            self.halt(f"MAX_DRAWDOWN {dd:.1f}%")
        elif self.cfg.session_profit_lock_pct > 0:
            start = sum(self.cfg.allocations.values())
            gain = 100.0 * (self.portfolio.total_equity() - start) / start
            if gain >= self.cfg.session_profit_lock_pct:
                self.halt(f"PROFIT_LOCK +{gain:.1f}%")

    def halt(self, reason: str) -> None:
        if self.halted:
            return
        self.halted = reason
        self.log.warning("HALTED (%s): cancelling %d resting orders; no new entries",
                         reason, len(self.orders))
        for oid in list(self.orders):
            if self.orders[oid].purpose == "entry":
                self.cancel_order(oid)

    # -- dashboard ----------------------------------------------------------
    async def dashboard_loop(self) -> None:
        while not self.stop_event.is_set():
            self.check_halts()
            self.render()
            try:
                await asyncio.wait_for(self.stop_event.wait(),
                                       timeout=self.cfg.dashboard_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def render(self) -> None:
        if self.cfg.clear_dashboard:
            sys.stdout.write("\x1b[2J\x1b[H")
        mode = "LIVE" if self.executor.live else "PAPER"
        lines = [f"Polymarket sports bot v2  [{mode}]  {utc_now():%H:%M:%S}Z  "
                 f"msgs={self.messages}  assets={len(self.registry)}"]
        if self.halted:
            lines.append(f"*** HALTED: {self.halted} — remove kill file / restart to resume ***")
        unreal = self.portfolio.unrealized()
        total_real = total_unreal = total_fees = 0.0
        for s in self.cfg.allocations:
            r, u, f = self.portfolio.realized[s], unreal[s], self.portfolio.fees_paid[s]
            total_real += r
            total_unreal += u
            total_fees += f
            lines.append(
                f"  {SPORT_LABEL[s]:<12} alloc={self.cfg.allocations[s]:>7.2f} "
                f"cash={self.portfolio.bankrolls[s]:>7.2f} "
                f"open={self.portfolio.sport_open_count(s)} "
                f"realized={r:+8.3f} unreal={u:+7.3f} fees={f:6.3f}")
        lines.append(f"  {'TOTAL':<12} equity={self.portfolio.total_equity():>8.2f} "
                     f"realized={total_real:+8.3f} unreal={total_unreal:+7.3f} "
                     f"fees={total_fees:6.3f} dd={self.portfolio.drawdown_pct():4.1f}% "
                     f"trades={len(self.portfolio.closed)} resting={len(self.orders)}")
        for t in self.portfolio.closed[-5:]:
            lines.append(f"    {t.closed_at:%H:%M:%S} {t.sport:<12} {t.strategy:<5} "
                         f"{t.slug[:34]:<34} {t.pnl:+.3f} {t.reason}")
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()

    # -- lifecycle ----------------------------------------------------------
    async def run(self) -> None:
        self.arm_executor()
        if not self.cfg.allocations:
            raise SystemExit("No sports enabled. Set SPORT_ALLOCATIONS, e.g. "
                             '"tennis=150,table_tennis=100,mlb=50"')
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop_event.set)
            except NotImplementedError:  # pragma: no cover
                pass
        tasks = [asyncio.create_task(self.discovery_loop()),
                 asyncio.create_task(self.websocket_supervisor()),
                 asyncio.create_task(self.dashboard_loop())]
        await self.stop_event.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for oid in list(self.orders):
            self.cancel_order(oid)
        self.log.info("shutdown: session realized %+.4f, fees %.4f, trades %d",
                      sum(self.portfolio.realized.values()),
                      sum(self.portfolio.fees_paid.values()),
                      len(self.portfolio.closed))


# ---------------------------------------------------------------------------
# self test (offline)
# ---------------------------------------------------------------------------

def run_self_test() -> None:
    os.environ["DRY_RUN"] = "true"
    cfg = RuntimeConfig(dry_run=True,
                        allocations={"tennis": 200.0, "table_tennis": 100.0},
                        trade_csv=Path("selftest_trades.csv"),
                        kill_file=Path("SELFTEST_STOP_NOPE"))
    bot = SportsTradingBot(cfg)

    # fee math
    f = taker_fee(100, 0.5, 0.05)
    assert abs(f - 1.25) < 1e-9, f
    assert taker_fee(100, 0.99, 0.05) < taker_fee(100, 0.5, 0.05)

    # classification order
    assert classify_sport("WTT - Men's Singles: A vs B") == "table_tennis"
    assert classify_sport("Setka Cup UA something") == "table_tennis"
    assert classify_sport("Wimbledon ATP: X vs Y") == "tennis"
    assert classify_sport("MLB: Yankees vs Red Sox") == "mlb"

    # registry with a complement pair
    now = utc_now()
    for tok, comp, outcome in (("T1", "T2", "Yes"), ("T2", "T1", "No")):
        bot.registry[tok] = MarketMeta(tok, comp, "M1", "ev", "atp-x-vs-y",
                                       "ATP: X vs Y?", outcome, "tennis", 0.01,
                                       now, None)
        bot.books[tok] = BookState(tick_size=0.01)

    # warm up with samples, then wide spread -> scalp quote
    st = bot.books["T1"]
    for i in range(20):
        st.best_bid, st.best_ask = 0.48, 0.52
        st.bid_size = st.ask_size = 100
        st.received_monotonic = time.monotonic()
        st.sample(time.monotonic() + i, 0.0, cfg.ewma_alpha)
    bot.on_bbo("T1", 0.48, 0.52, 100, 100)
    assert any(o.strategy == "scalp" for o in bot.orders.values()), st.last_reason

    # ask crosses down through our bid -> paper fill -> position + maker exit
    bot.on_bbo("T1", 0.47, 0.49, 100, 100)
    assert "T1" in bot.portfolio.positions
    assert any(o.purpose == "exit" for o in bot.orders.values())

    # bid rallies through exit price -> maker TP
    bot.on_bbo("T1", 0.60, 0.62, 100, 100)
    assert "T1" not in bot.portfolio.positions
    assert bot.portfolio.closed and bot.portfolio.closed[-1].pnl > 0

    # complement arb: asks sum to 0.97 -> locked profit both legs
    bot.portfolio.last_trade_at.clear()
    b1, b2 = bot.books["T1"], bot.books["T2"]
    b1.best_bid, b1.best_ask, b1.bid_size, b1.ask_size = 0.46, 0.47, 200, 200
    b2.best_bid, b2.best_ask, b2.bid_size, b2.ask_size = 0.49, 0.50, 200, 200
    b2.received_monotonic = time.monotonic()
    for i in range(20):
        b2.sample(time.monotonic() + 100 + i, 0.0, cfg.ewma_alpha)
    bot.on_bbo("T1", 0.46, 0.47, 200, 200)
    arb_positions = [p for p in bot.portfolio.positions.values() if p.strategy == "arb"]
    assert len(arb_positions) == 2, bot.books["T1"].last_reason
    assert sum(p.arb_locked_pnl or 0 for p in arb_positions) > 0

    # drawdown halt cancels entry quotes
    bot.portfolio.equity_high = bot.portfolio.total_equity() * 2
    bot.check_halts()
    assert bot.halted and not any(o.purpose == "entry" for o in bot.orders.values())

    Path("selftest_trades.csv").unlink(missing_ok=True)
    print("SELF-TEST PASS: fees, classification, scalp quote+fill, maker TP, "
          "complement arb, drawdown halt")


# ---------------------------------------------------------------------------

def configure_logging(cfg: RuntimeConfig) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(cfg.log_file)],
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--alloc", help='e.g. "tennis=150,table_tennis=100,mlb=50" '
                                    "(overrides SPORT_ALLOCATIONS)")
    args = ap.parse_args()
    if args.self_test:
        run_self_test()
        return
    if args.alloc:
        os.environ["SPORT_ALLOCATIONS"] = args.alloc
    cfg = RuntimeConfig()
    configure_logging(cfg)
    asyncio.run(SportsTradingBot(cfg).run())


if __name__ == "__main__":
    main()
