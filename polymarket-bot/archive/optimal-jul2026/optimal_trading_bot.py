#!/usr/bin/env python3
"""
Polymarket Sports Bot — OPTIMAL build (consolidated from V2/V7/HFT iterations).

What this version does:
- Discovers active Baseball and Tennis markets from the Gamma events API.
- Subscribes to public Polymarket market-data WebSocket quotes.
- Maintains real best-bid/best-ask histories.
- Opens and closes positions for safe/base/risky models.
- Uses ask prices for entries and bid prices for exits, so spread cost is real.
- Models the July-2026 Polymarket sports taker fee: fee = RATE * p * (1-p) * shares.
- Enforces asymmetric risk: take-profit targets are fee-and-spread aware.
- Writes every entry/exit to CSV and logs operational errors.

Execution modes:
- DRY_RUN=true (default): simulated fills against live quotes. Safe.
- DRY_RUN=false: routes real orders via py-clob-client. Refuses to start unless
  LIVE_CONFIRM=I_UNDERSTAND_LIVE_RISK and POLYMARKET_PRIVATE_KEY / POLYMARKET_FUNDER
  are set. Never set these until the dry run has proven the strategy.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import math
import os
import signal
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Deque, Iterable

import requests
import websockets

# ============================================================
# RUNTIME CONFIGURATION
# ============================================================
DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "on"}
MAX_RUNTIME_SECONDS = float(os.getenv("MAX_RUNTIME_SECONDS", "1800"))
DASHBOARD_INTERVAL_SECONDS = float(os.getenv("DASHBOARD_INTERVAL_SECONDS", "5"))
STRATEGY_INTERVAL_SECONDS = float(os.getenv("STRATEGY_INTERVAL_SECONDS", "1"))
DISCOVERY_RETRY_SECONDS = float(os.getenv("DISCOVERY_RETRY_SECONDS", "30"))
MAX_DISCOVERY_EVENTS = int(os.getenv("MAX_DISCOVERY_EVENTS", "1000"))
MAX_SUBSCRIPTION_ASSETS = int(os.getenv("MAX_SUBSCRIPTION_ASSETS", "400"))
QUOTE_STALE_SECONDS = float(os.getenv("QUOTE_STALE_SECONDS", "20"))
MIN_SAMPLE_INTERVAL_SECONDS = float(os.getenv("MIN_SAMPLE_INTERVAL_SECONDS", "0.75"))
COOLDOWN_SECONDS = float(os.getenv("COOLDOWN_SECONDS", "60"))
MAX_OPEN_POSITIONS_PER_MODEL = int(os.getenv("MAX_OPEN_POSITIONS_PER_MODEL", "3"))
MIN_TRADE_NOTIONAL = float(os.getenv("MIN_TRADE_NOTIONAL", "1.00"))
MAX_TRADE_NOTIONAL = float(os.getenv("MAX_TRADE_NOTIONAL", "15.00"))
BOOK_DEPTH_USAGE_FRACTION = float(os.getenv("BOOK_DEPTH_USAGE_FRACTION", "0.25"))
# Bootstrap trades force an entry to prove the pipeline works. Useful for demos,
# poison for measuring a strategy. OFF by default in this build.
BOOTSTRAP_TRADES = os.getenv("BOOTSTRAP_TRADES", "false").strip().lower() in {"1", "true", "yes", "on"}
BOOTSTRAP_AFTER_SECONDS = float(os.getenv("BOOTSTRAP_AFTER_SECONDS", "45"))
BOOTSTRAP_MIN_SCORE = float(os.getenv("BOOTSTRAP_MIN_SCORE", "-0.10"))
# Polymarket sports taker fee (July 2026 schedule): fee = RATE * price * (1-price) * shares.
# Peaks at ~1.25% of notional at p=0.50, falls toward zero at the extremes.
# Maker (resting limit) orders pay zero; this build enters as a taker, so it pays this.
SPORTS_FEE_RATE = float(os.getenv("SPORTS_FEE_RATE", "0.05"))
# Fee-lever knobs (see runbook):
# Entry band: fee/share = RATE*p*(1-p) is ~2x cheaper at 0.80 than at 0.50.
# Default band targets live favorites, where momentum entries also align with drift.
ENTRY_MIN_MID = float(os.getenv("ENTRY_MIN_MID", "0.55"))
ENTRY_MAX_MID = float(os.getenv("ENTRY_MAX_MID", "0.90"))
# Global multiplier on per-model risk fractions. 1.0 = calibrated defaults.
# Dry-run experimentation up to 2-3x is reasonable; hard-capped at 35% of bucket.
RISK_MULTIPLIER = float(os.getenv("RISK_MULTIPLIER", "1.0"))
# Reversal-exit sensitivity: threshold = move_trigger * this. Higher = fewer
# churn exits (each churn exit pays a full round-trip fee for ~zero move).
REVERSAL_TRIGGER_MULT = float(os.getenv("REVERSAL_TRIGGER_MULT", "1.0"))
# Live-mode gating. All three must be deliberately set for real orders.
LIVE_CONFIRM = os.getenv("LIVE_CONFIRM", "").strip()
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
POLYMARKET_FUNDER = os.getenv("POLYMARKET_FUNDER", "").strip()
POLYMARKET_SIGNATURE_TYPE = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1"))
CLOB_HOST = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
LOG_FILE = Path(os.getenv("LOG_FILE", "sports_trading_bot_v2.log"))
TRADE_CSV = Path(os.getenv("TRADE_CSV", "dry_run_trades_optimal.csv"))
PROGRESS_FILE = Path(os.getenv("PROGRESS_FILE", "progress_optimal.log"))
TICK_RECORDING = os.getenv("TICK_RECORDING", "true").strip().lower() in {"1", "true", "yes", "on"}
TICK_RECORD_FILE = Path(os.getenv("TICK_RECORD_FILE", "real_ticks.csv"))
CLEAR_DASHBOARD = os.getenv("CLEAR_DASHBOARD", "true").strip().lower() in {"1", "true", "yes", "on"}

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Capital: BANKROLL_PER_SPORT is split EQUALLY across the three models.
# Default 75 -> $150 total deployment ($75 Tennis + $75 Baseball, $25/model).
BANKROLL_PER_SPORT = float(os.getenv("BANKROLL_PER_SPORT", "75"))
TOTAL_CAPITAL = BANKROLL_PER_SPORT * 2
_PER_MODEL = round(BANKROLL_PER_SPORT / 3.0, 2)
ALLOCATIONS = {
    "Tennis": {"safe": _PER_MODEL, "base": _PER_MODEL, "risky": _PER_MODEL},
    "Baseball": {"safe": _PER_MODEL, "base": _PER_MODEL, "risky": _PER_MODEL},
}
MODELS = ("safe", "base", "risky")
SPORTS = ("Tennis", "Baseball")

TENNIS_TERMS = (
    "tennis", " atp ", " wta ", "challenger", " itf ", "roland garros",
    "wimbledon", "us open tennis", "australian open tennis",
)
BASEBALL_TERMS = (
    "baseball", " mlb ", "world series", "american league", "national league",
    "spring training",
)

# ============================================================
# LOGGING
# ============================================================
logger = logging.getLogger("sports-bot-v2")
logger.setLevel(logging.DEBUG)
logger.propagate = False

if not logger.handlers:
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

# ============================================================
# DATA MODELS
# ============================================================
@dataclass(frozen=True)
class RiskProfile:
    risk_fraction: float
    take_profit: float
    stop_loss: float
    max_spread: float
    move_trigger: float
    lookback_samples: int
    max_hold_seconds: float
    minimum_depth_shares: float


def get_profile(sport: str, model: str) -> RiskProfile:
    """Return the V2 profile while adding parameters required for execution."""
    # ASYMMETRIC RISK, FEE-AWARE.
    # A taker round trip at p=0.50 costs ~0.025/share in fees alone (0.0125 each way
    # at SPORTS_FEE_RATE=0.05), before spread. The old V2 targets (tp 0.007 baseball)
    # were structurally unprofitable under the July-2026 fee schedule: every
    # "winning" trade lost money net of fees. TP targets below clear
    # round-trip fee + typical spread with margin; every profile's TP > SL.
    base_configs = {
        "Baseball": {
            "tp": 0.032,
            "sl": 0.020,
            "spread": 0.014,
            "trigger": 0.004,
        },
        "Tennis": {
            "tp": 0.042,
            "sl": 0.026,
            "spread": 0.024,
            "trigger": 0.006,
        },
    }

    model_mods = {
        "safe": {
            "risk": 0.02,
            "spread_mult": 0.70,
            "trigger_mult": 1.35,
            "lookback": 12,
            "hold": 900.0,
            "depth": 40.0,
        },
        "base": {
            "risk": 0.05,
            "spread_mult": 1.00,
            "trigger_mult": 1.00,
            "lookback": 8,
            "hold": 600.0,
            "depth": 20.0,
        },
        "risky": {
            "risk": 0.10,
            "spread_mult": 1.30,
            "trigger_mult": 0.70,
            "lookback": 4,
            "hold": 360.0,
            "depth": 10.0,
        },
    }

    if sport not in base_configs:
        raise ValueError(f"Unsupported sport: {sport}")
    if model not in model_mods:
        raise ValueError(f"Unsupported model: {model}")

    base = base_configs[sport]
    mod = model_mods[model]
    return RiskProfile(
        risk_fraction=min(0.35, mod["risk"] * RISK_MULTIPLIER),
        take_profit=base["tp"],
        stop_loss=base["sl"],
        max_spread=base["spread"] * mod["spread_mult"],
        move_trigger=base["trigger"] * mod["trigger_mult"],
        lookback_samples=mod["lookback"],
        max_hold_seconds=mod["hold"],
        minimum_depth_shares=mod["depth"],
    )


@dataclass
class MarketMeta:
    token_id: str
    condition_id: str
    sport: str
    question: str
    outcome: str
    slug: str
    tick_size: float = 0.01


@dataclass
class QuoteState:
    best_bid: float | None = None
    best_ask: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    last_exchange_timestamp: str | None = None
    updated_monotonic: float = 0.0
    last_sample_monotonic: float = 0.0
    tick_size: float = 0.01
    mids: Deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=120))

    @property
    def valid(self) -> bool:
        if self.best_bid is None or self.best_ask is None:
            return False
        return 0.0 <= self.best_bid < self.best_ask <= 1.0

    @property
    def spread(self) -> float | None:
        if not self.valid:
            return None
        return self.best_ask - self.best_bid

    @property
    def mid(self) -> float | None:
        if not self.valid:
            return None
        return (self.best_bid + self.best_ask) / 2.0


@dataclass
class Position:
    sport: str
    model: str
    token_id: str
    condition_id: str
    question: str
    outcome: str
    quantity: float
    entry_price: float
    entry_notional: float
    entry_fee: float
    opened_monotonic: float
    opened_utc: str
    entry_reason: str
    entry_signal_score: float
    highest_bid: float


@dataclass
class Candidate:
    meta: MarketMeta
    quote: QuoteState
    profile: RiskProfile
    momentum: float
    trigger: float
    score: float


class PortfolioStats:
    def __init__(self) -> None:
        self.starting_bankrolls = {
            sport: dict(models) for sport, models in ALLOCATIONS.items()
        }
        self.cash = {sport: dict(models) for sport, models in ALLOCATIONS.items()}
        self.realized_pnl = {
            sport: {model: 0.0 for model in MODELS} for sport in SPORTS
        }
        self.positions: dict[tuple[str, str, str], Position] = {}
        self.cooldowns: dict[tuple[str, str, str], float] = {}
        self.bootstrap_used: set[tuple[str, str]] = set()
        self.entries = 0
        self.closed_trades = 0
        self.wins = 0
        self.losses = 0
        self.exit_reasons: Counter[str] = Counter()
        self.wait_reasons: Counter[str] = Counter()
        self.messages_received = 0
        self.quote_updates = 0
        self.ws_reconnects = 0
        self.discovery_count = 0
        self.last_message_monotonic = 0.0
        self.started_monotonic = time.monotonic()

    def position_key(self, sport: str, model: str, condition_id: str) -> tuple[str, str, str]:
        return sport, model, condition_id

    def open_count(self, sport: str, model: str) -> int:
        return sum(
            1 for position in self.positions.values()
            if position.sport == sport and position.model == model
        )

    def position_value(self, sport: str, model: str, quotes: dict[str, QuoteState]) -> float:
        total = 0.0
        for position in self.positions.values():
            if position.sport != sport or position.model != model:
                continue
            quote = quotes.get(position.token_id)
            mark = quote.best_bid if quote and quote.best_bid is not None else position.entry_price
            total += position.quantity * mark
        return total

    def unrealized_pnl(self, sport: str, model: str, quotes: dict[str, QuoteState]) -> float:
        total = 0.0
        for position in self.positions.values():
            if position.sport != sport or position.model != model:
                continue
            quote = quotes.get(position.token_id)
            mark = quote.best_bid if quote and quote.best_bid is not None else position.entry_price
            total += (
                position.quantity * (mark - position.entry_price)
                - position.entry_fee
                - taker_fee(mark, position.quantity)  # projected exit fee
            )
        return total

    def equity(self, sport: str, model: str, quotes: dict[str, QuoteState]) -> float:
        return self.cash[sport][model] + self.position_value(sport, model, quotes)


# ============================================================
# HELPERS
# ============================================================
def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(flatten_text(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(flatten_text(v) for v in value)
    return str(value or "")


def classify_sport(text: str) -> str | None:
    normalized = f" {text.lower()} "
    if any(term in normalized for term in BASEBALL_TERMS):
        return "Baseball"
    if any(term in normalized for term in TENNIS_TERMS):
        return "Tennis"
    return None


def normalize_messages(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def top_level(levels: Any, side: str) -> tuple[float | None, float | None]:
    """Return best price and size without trusting incoming sort order."""
    parsed: list[tuple[float, float]] = []
    if not isinstance(levels, list):
        return None, None
    for level in levels:
        if not isinstance(level, dict):
            continue
        price = safe_float(level.get("price"))
        size = safe_float(level.get("size"))
        if price is None or size is None or size < 0:
            continue
        parsed.append((price, size))
    if not parsed:
        return None, None
    return max(parsed, key=lambda x: x[0]) if side == "bid" else min(parsed, key=lambda x: x[0])


def rolling_momentum(quote: QuoteState, lookback_samples: int) -> float | None:
    if len(quote.mids) <= lookback_samples:
        return None
    current_mid = quote.mids[-1][1]
    previous_mid = quote.mids[-1 - lookback_samples][1]
    return current_mid - previous_mid


def rolling_abs_move(quote: QuoteState, window: int = 20) -> float:
    values = [mid for _, mid in list(quote.mids)[-window:]]
    if len(values) < 3:
        return 0.0
    changes = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
    changes.sort()
    middle = len(changes) // 2
    if len(changes) % 2:
        return changes[middle]
    return (changes[middle - 1] + changes[middle]) / 2.0


def taker_fee(price: float, shares: float) -> float:
    """Polymarket sports taker fee: RATE * p * (1-p) * shares (July 2026 schedule)."""
    p = min(1.0, max(0.0, price))
    return SPORTS_FEE_RATE * p * (1.0 - p) * shares


class LiveExecutor:
    """Routes real orders via py-clob-client. Only constructed when DRY_RUN=false."""

    def __init__(self) -> None:
        from py_clob_client.client import ClobClient  # deferred: not needed in dry run
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL

        self._MarketOrderArgs = MarketOrderArgs
        self._OrderType = OrderType
        self._BUY, self._SELL = BUY, SELL
        self.client = ClobClient(
            CLOB_HOST,
            key=POLYMARKET_PRIVATE_KEY,
            chain_id=137,
            signature_type=POLYMARKET_SIGNATURE_TYPE,
            funder=POLYMARKET_FUNDER,
        )
        self.client.set_api_creds(self.client.create_or_derive_api_creds())
        logger.warning("LIVE EXECUTOR INITIALIZED — real orders will be placed.")

    def market_buy(self, token_id: str, usdc_amount: float) -> dict[str, Any]:
        args = self._MarketOrderArgs(
            token_id=token_id, amount=round(usdc_amount, 2),
            side=self._BUY, order_type=self._OrderType.FOK,
        )
        signed = self.client.create_market_order(args)
        return self.client.post_order(signed, self._OrderType.FOK)

    def market_sell(self, token_id: str, shares: float) -> dict[str, Any]:
        args = self._MarketOrderArgs(
            token_id=token_id, amount=round(shares, 4),
            side=self._SELL, order_type=self._OrderType.FOK,
        )
        signed = self.client.create_market_order(args)
        return self.client.post_order(signed, self._OrderType.FOK)


def initialize_trade_csv() -> None:
    if TRADE_CSV.exists() and TRADE_CSV.stat().st_size > 0:
        return
    with TRADE_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "timestamp_utc", "action", "sport", "model", "condition_id",
                "token_id", "outcome", "question", "quantity", "price",
                "notional", "fee", "pnl", "reason", "signal_score",
                "hold_seconds",
            ]
        )


def append_trade_csv(
    *,
    action: str,
    position: Position,
    price: float,
    notional: float,
    fee: float,
    pnl: float,
    reason: str,
    hold_seconds: float,
) -> None:
    with TRADE_CSV.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                utc_now_text(), action, position.sport, position.model,
                position.condition_id, position.token_id, position.outcome,
                position.question, f"{position.quantity:.8f}", f"{price:.6f}",
                f"{notional:.6f}", f"{fee:.6f}", f"{pnl:.6f}", reason,
                f"{position.entry_signal_score:.4f}", f"{hold_seconds:.1f}",
            ]
        )


# ============================================================
# BOT
# ============================================================
class SportsTradingBot:
    def __init__(self) -> None:
        self.stats = PortfolioStats()
        self.registry: dict[str, MarketMeta] = {}
        self.quotes: dict[str, QuoteState] = {}
        self.stop_event = asyncio.Event()
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": "polymarket-sports-dry-run-v2/2.0"})
        self.ws_connected = False
        self.last_error = ""
        self.live_executor: LiveExecutor | None = None
        self._tick_handle = None

    async def discover_markets(self) -> None:
        """Discover active sports markets, retrying until at least one is found."""
        while not self.stop_event.is_set():
            try:
                registry = await asyncio.to_thread(self._discover_markets_sync)
                if not registry:
                    raise RuntimeError("No active Baseball or Tennis token IDs were discovered")
                self.registry = registry
                for token_id, meta in registry.items():
                    quote = self.quotes.setdefault(token_id, QuoteState())
                    quote.tick_size = meta.tick_size
                self.stats.discovery_count += 1
                logger.info(
                    "Discovered %s assets across %s conditions",
                    len(self.registry), len({m.condition_id for m in self.registry.values()}),
                )
                return
            except Exception as exc:
                self.last_error = f"Discovery: {exc}"
                logger.exception("Market discovery failed")
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=DISCOVERY_RETRY_SECONDS)
                except asyncio.TimeoutError:
                    pass

    def _discover_markets_sync(self) -> dict[str, MarketMeta]:
        registry: dict[str, MarketMeta] = {}
        page_size = 100
        # Gamma rejects some optional params over time (422s). Start minimal —
        # the keyword filters downstream do the real selection work anyway.
        base_params = {"active": "true", "closed": "false"}

        for offset in range(0, MAX_DISCOVERY_EVENTS, page_size):
            response = self.http.get(
                GAMMA_EVENTS_URL,
                params={**base_params, "limit": page_size, "offset": offset},
                timeout=20,
            )
            if response.status_code == 422 and base_params:
                # Retry this page with no optional params at all before failing.
                logger.warning(
                    "Gamma 422 with params %s; retrying with limit/offset only",
                    base_params,
                )
                base_params = {}
                response = self.http.get(
                    GAMMA_EVENTS_URL,
                    params={"limit": page_size, "offset": offset},
                    timeout=20,
                )
            response.raise_for_status()
            events = response.json()
            if not isinstance(events, list):
                raise RuntimeError(f"Unexpected Gamma response type: {type(events).__name__}")

            for event in events:
                if not isinstance(event, dict):
                    continue
                event_text = " ".join(
                    flatten_text(event.get(key))
                    for key in ("title", "description", "slug", "ticker", "tags", "series")
                )

                markets = event.get("markets") or []
                if not isinstance(markets, list):
                    continue

                for market in markets:
                    if not isinstance(market, dict):
                        continue
                    if market.get("active") is False or market.get("closed") is True:
                        continue
                    if market.get("enableOrderBook") is False:
                        continue

                    question = str(market.get("question") or event.get("title") or "Unknown market")
                    market_text = " ".join(
                        (
                            event_text,
                            question,
                            flatten_text(market.get("tags")),
                            flatten_text(market.get("sportsMarketType")),
                            flatten_text(market.get("groupItemTitle")),
                            flatten_text(market.get("slug")),
                        )
                    )
                    sport = classify_sport(market_text)
                    if sport is None:
                        continue

                    token_ids = parse_json_list(
                        market.get("clobTokenIds")
                        or market.get("clob_token_ids")
                        or market.get("assets_ids")
                    )
                    outcomes = parse_json_list(market.get("outcomes"))
                    if not token_ids:
                        continue

                    condition_id = str(
                        market.get("conditionId")
                        or market.get("condition_id")
                        or market.get("market")
                        or market.get("id")
                        or market.get("slug")
                        or question
                    )
                    slug = str(market.get("slug") or event.get("slug") or condition_id)
                    tick_size = safe_float(
                        market.get("orderPriceMinTickSize")
                        or market.get("order_price_min_tick_size")
                    ) or 0.01

                    for index, raw_token in enumerate(token_ids):
                        token_id = str(raw_token)
                        if not token_id or token_id == "None":
                            continue
                        outcome = str(outcomes[index]) if index < len(outcomes) else f"Outcome {index + 1}"
                        registry[token_id] = MarketMeta(
                            token_id=token_id,
                            condition_id=condition_id,
                            sport=sport,
                            question=question,
                            outcome=outcome,
                            slug=slug,
                            tick_size=tick_size,
                        )
                        if len(registry) >= MAX_SUBSCRIPTION_ASSETS:
                            return registry

            if len(events) < page_size or len(registry) >= MAX_SUBSCRIPTION_ASSETS:
                break

        return registry

    async def websocket_loop(self) -> None:
        while not self.stop_event.is_set():
            if not self.registry:
                await asyncio.sleep(1)
                continue

            try:
                self.stats.ws_reconnects += 1
                async with websockets.connect(
                    MARKET_WS_URL,
                    ping_interval=None,
                    open_timeout=20,
                    close_timeout=5,
                    max_size=8_000_000,
                ) as ws:
                    subscription = {
                        "assets_ids": list(self.registry.keys()),
                        "type": "market",
                        "custom_feature_enabled": True,
                    }
                    await ws.send(json.dumps(subscription))
                    self.ws_connected = True
                    logger.info("WebSocket subscribed to %s assets", len(self.registry))

                    heartbeat_task = asyncio.create_task(self._heartbeat(ws))
                    try:
                        async for raw in ws:
                            if self.stop_event.is_set():
                                break
                            if raw in ("PONG", b"PONG"):
                                continue
                            await self.handle_raw_message(raw)
                    finally:
                        heartbeat_task.cancel()
                        await asyncio.gather(heartbeat_task, return_exceptions=True)
                        self.ws_connected = False

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.ws_connected = False
                self.last_error = f"WebSocket: {exc}"
                logger.exception("WebSocket connection failed")
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass

    async def _heartbeat(self, ws: Any) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(10)
            await ws.send("PING")

    async def handle_raw_message(self, raw: str | bytes) -> None:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.debug("Ignoring non-JSON WebSocket message: %r", raw)
            return

        messages = normalize_messages(payload)
        self.stats.messages_received += len(messages)
        self.stats.last_message_monotonic = time.monotonic()

        for message in messages:
            event_type = message.get("event_type")
            if event_type == "book":
                self._handle_book(message)
            elif event_type == "best_bid_ask":
                self._handle_best_bid_ask(message)
            elif event_type == "price_change":
                self._handle_price_change(message)
            elif event_type == "tick_size_change":
                self._handle_tick_size_change(message)
            elif event_type == "market_resolved":
                self._handle_market_resolved(message)

    def _quote_for(self, token_id: str) -> QuoteState | None:
        if token_id not in self.registry:
            return None
        return self.quotes.setdefault(token_id, QuoteState(tick_size=self.registry[token_id].tick_size))

    def _update_quote(
        self,
        token_id: str,
        *,
        bid: float | None = None,
        ask: float | None = None,
        bid_size: float | None = None,
        ask_size: float | None = None,
        exchange_timestamp: Any = None,
    ) -> None:
        quote = self._quote_for(token_id)
        if quote is None:
            return

        if bid is not None:
            quote.best_bid = bid
        if ask is not None:
            quote.best_ask = ask
        if bid_size is not None:
            quote.bid_size = bid_size
        if ask_size is not None:
            quote.ask_size = ask_size

        now = time.monotonic()
        quote.updated_monotonic = now
        quote.last_exchange_timestamp = str(exchange_timestamp or "")
        self.stats.quote_updates += 1

        mid = quote.mid
        if mid is not None and (
            not quote.mids or now - quote.last_sample_monotonic >= MIN_SAMPLE_INTERVAL_SECONDS
        ):
            quote.mids.append((now, mid))
            quote.last_sample_monotonic = now
            self._record_tick(token_id, quote)

    def _record_tick(self, token_id: str, quote: QuoteState) -> None:
        """Append sampled real quotes to TICK_RECORD_FILE for later replay
        optimization. This is the project's genuine historical dataset."""
        if not TICK_RECORDING:
            return
        try:
            if self._tick_handle is None:
                new_file = not TICK_RECORD_FILE.exists() or TICK_RECORD_FILE.stat().st_size == 0
                self._tick_handle = TICK_RECORD_FILE.open("a", encoding="utf-8")
                if new_file:
                    self._tick_handle.write(
                        "timestamp_utc,token_id,best_bid,best_ask,bid_size,ask_size\n"
                    )
            self._tick_handle.write(
                f"{utc_now_text()},{token_id},"
                f"{'' if quote.best_bid is None else f'{quote.best_bid:.4f}'},"
                f"{'' if quote.best_ask is None else f'{quote.best_ask:.4f}'},"
                f"{'' if quote.bid_size is None else f'{quote.bid_size:.2f}'},"
                f"{'' if quote.ask_size is None else f'{quote.ask_size:.2f}'}\n"
            )
        except Exception:
            logger.exception("Tick record write failed")

    def _handle_book(self, message: dict[str, Any]) -> None:
        token_id = str(message.get("asset_id") or "")
        bid, bid_size = top_level(message.get("bids"), "bid")
        ask, ask_size = top_level(message.get("asks"), "ask")
        self._update_quote(
            token_id,
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            exchange_timestamp=message.get("timestamp"),
        )

    def _handle_best_bid_ask(self, message: dict[str, Any]) -> None:
        token_id = str(message.get("asset_id") or "")
        self._update_quote(
            token_id,
            bid=safe_float(message.get("best_bid")),
            ask=safe_float(message.get("best_ask")),
            exchange_timestamp=message.get("timestamp"),
        )

    def _handle_price_change(self, message: dict[str, Any]) -> None:
        changes = message.get("price_changes") or []
        if not isinstance(changes, list):
            return
        for change in changes:
            if not isinstance(change, dict):
                continue
            token_id = str(change.get("asset_id") or "")
            self._update_quote(
                token_id,
                bid=safe_float(change.get("best_bid")),
                ask=safe_float(change.get("best_ask")),
                exchange_timestamp=message.get("timestamp"),
            )

    def _handle_tick_size_change(self, message: dict[str, Any]) -> None:
        token_id = str(message.get("asset_id") or "")
        new_tick = safe_float(message.get("new_tick_size"))
        quote = self._quote_for(token_id)
        if quote is not None and new_tick is not None and new_tick > 0:
            quote.tick_size = new_tick
            self.registry[token_id].tick_size = new_tick

    def _handle_market_resolved(self, message: dict[str, Any]) -> None:
        winning_token = str(message.get("winning_asset_id") or "")
        assets = {str(token) for token in parse_json_list(message.get("assets_ids"))}
        if winning_token:
            assets.add(winning_token)
        for token_id in assets:
            quote = self._quote_for(token_id)
            if quote is None:
                continue
            price = 1.0 if token_id == winning_token else 0.0
            quote.best_bid = price
            quote.best_ask = price
            quote.updated_monotonic = time.monotonic()

    def _eligible_candidate(self, token_id: str, model: str) -> Candidate | None:
        meta = self.registry[token_id]
        quote = self.quotes.get(token_id)
        profile = get_profile(meta.sport, model)
        now = time.monotonic()

        if quote is None or not quote.valid:
            self.stats.wait_reasons["invalid_book"] += 1
            return None
        if now - quote.updated_monotonic > QUOTE_STALE_SECONDS:
            self.stats.wait_reasons["stale_book"] += 1
            return None

        spread = quote.spread
        mid = quote.mid
        if spread is None or mid is None:
            return None

        # A one-tick spread is always eligible even when the historical safe
        # threshold is fractionally below the current market tick.
        effective_max_spread = max(profile.max_spread, quote.tick_size + 1e-9)
        if spread > effective_max_spread:
            self.stats.wait_reasons["wide_spread"] += 1
            return None
        if mid < ENTRY_MIN_MID or mid > ENTRY_MAX_MID:
            self.stats.wait_reasons["extreme_price"] += 1
            return None
        if quote.ask_size is not None and quote.ask_size < profile.minimum_depth_shares:
            self.stats.wait_reasons["low_depth"] += 1
            return None

        momentum = rolling_momentum(quote, profile.lookback_samples)
        if momentum is None:
            self.stats.wait_reasons["warming_up"] += 1
            return None

        observed_move = rolling_abs_move(quote)
        # Keep triggers achievable on 0.01-tick books while retaining the
        # sport/model calibration as an upper bound.
        adaptive_trigger = max(
            quote.tick_size * 0.5,
            min(profile.move_trigger, max(observed_move * 1.25, quote.tick_size * 0.5)),
        )
        score = momentum / adaptive_trigger if adaptive_trigger > 0 else 0.0
        return Candidate(meta, quote, profile, momentum, adaptive_trigger, score)

    async def strategy_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.manage_exits()
                self.manage_entries()
            except Exception:
                logger.exception("Strategy evaluation failed")
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), timeout=STRATEGY_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                pass

    def manage_entries(self) -> None:
        if not self.registry:
            return
        now = time.monotonic()
        runtime = now - self.stats.started_monotonic

        for sport in SPORTS:
            sport_tokens = [token for token, meta in self.registry.items() if meta.sport == sport]
            if not sport_tokens:
                continue

            for model in MODELS:
                if self.stats.open_count(sport, model) >= MAX_OPEN_POSITIONS_PER_MODEL:
                    continue
                if self.stats.cash[sport][model] < MIN_TRADE_NOTIONAL:
                    continue

                candidates: list[Candidate] = []
                for token_id in sport_tokens:
                    meta = self.registry[token_id]
                    key = self.stats.position_key(sport, model, meta.condition_id)
                    if key in self.stats.positions:
                        continue
                    if now < self.stats.cooldowns.get(key, 0.0):
                        continue
                    candidate = self._eligible_candidate(token_id, model)
                    if candidate is not None:
                        candidates.append(candidate)

                if not candidates:
                    continue

                candidates.sort(
                    key=lambda c: (
                        c.score,
                        c.quote.ask_size or 0.0,
                        -(c.quote.spread or 1.0),
                    ),
                    reverse=True,
                )
                best = candidates[0]

                entry_reason = "momentum"
                should_enter = best.score >= 1.0

                bootstrap_key = (sport, model)
                if (
                    not should_enter
                    and BOOTSTRAP_TRADES
                    and runtime >= BOOTSTRAP_AFTER_SECONDS
                    and bootstrap_key not in self.stats.bootstrap_used
                    and best.score >= BOOTSTRAP_MIN_SCORE
                ):
                    should_enter = True
                    entry_reason = "bootstrap_live_quote"
                    self.stats.bootstrap_used.add(bootstrap_key)

                if should_enter:
                    self.open_position(best, model, entry_reason)

    def open_position(self, candidate: Candidate, model: str, reason: str) -> None:
        meta = candidate.meta
        quote = candidate.quote
        profile = candidate.profile
        if quote.best_ask is None:
            return

        cash = self.stats.cash[meta.sport][model]
        desired_notional = max(MIN_TRADE_NOTIONAL, cash * profile.risk_fraction)
        desired_notional = min(desired_notional, MAX_TRADE_NOTIONAL, cash)

        quantity = desired_notional / quote.best_ask
        if quote.ask_size is not None and quote.ask_size > 0:
            quantity = min(quantity, quote.ask_size * BOOK_DEPTH_USAGE_FRACTION)
        quantity = max(0.0, quantity)
        notional = quantity * quote.best_ask
        if notional < min(MIN_TRADE_NOTIONAL, cash):
            self.stats.wait_reasons["notional_too_small"] += 1
            return

        entry_fee = taker_fee(quote.best_ask, quantity)
        total_cost = notional + entry_fee
        if total_cost > cash:
            scale = cash / total_cost
            quantity = max(0.0, quantity * scale)
            notional = quantity * quote.best_ask
            entry_fee = taker_fee(quote.best_ask, quantity)
            total_cost = notional + entry_fee
        if total_cost <= 0:
            return

        if not DRY_RUN:
            if self.live_executor is None:
                raise RuntimeError("DRY_RUN=false but live executor is not initialized.")
            try:
                response = self.live_executor.market_buy(meta.token_id, notional)
                logger.info("LIVE BUY response: %s", response)
            except Exception:
                logger.exception("Live buy failed; position not recorded")
                self.stats.wait_reasons["live_order_failed"] += 1
                return

        position = Position(
            sport=meta.sport,
            model=model,
            token_id=meta.token_id,
            condition_id=meta.condition_id,
            question=meta.question,
            outcome=meta.outcome,
            quantity=quantity,
            entry_price=quote.best_ask,
            entry_notional=notional,
            entry_fee=entry_fee,
            opened_monotonic=time.monotonic(),
            opened_utc=utc_now_text(),
            entry_reason=reason,
            entry_signal_score=candidate.score,
            highest_bid=quote.best_bid or quote.best_ask,
        )
        key = self.stats.position_key(meta.sport, model, meta.condition_id)
        self.stats.positions[key] = position
        self.stats.cash[meta.sport][model] -= total_cost
        self.stats.entries += 1

        append_trade_csv(
            action="OPEN",
            position=position,
            price=position.entry_price,
            notional=position.entry_notional,
            fee=position.entry_fee,
            pnl=0.0,
            reason=reason,
            hold_seconds=0.0,
        )
        logger.info(
            "OPEN %s %s %s %s qty=%.4f ask=%.4f score=%.2f reason=%s",
            meta.sport, model, meta.outcome, meta.slug,
            quantity, position.entry_price, candidate.score, reason,
        )

    def manage_exits(self) -> None:
        now = time.monotonic()
        for key, position in list(self.stats.positions.items()):
            quote = self.quotes.get(position.token_id)
            if quote is None or quote.best_bid is None:
                continue

            profile = get_profile(position.sport, position.model)
            current_bid = quote.best_bid
            position.highest_bid = max(position.highest_bid, current_bid)
            hold_seconds = now - position.opened_monotonic
            price_pnl = current_bid - position.entry_price

            # Fee-aware TP: the profile target is a floor. The realized target must
            # also clear the round-trip taker fee at current prices plus one tick,
            # so a "take_profit" exit is always net-positive after fees.
            round_trip_fee_per_share = (
                taker_fee(position.entry_price, 1.0) + taker_fee(current_bid, 1.0)
            )
            required_tp = max(
                profile.take_profit, round_trip_fee_per_share + quote.tick_size
            )

            reason: str | None = None
            if price_pnl >= required_tp:
                reason = "take_profit"
            elif price_pnl <= -profile.stop_loss:
                reason = "stop_loss"
            elif hold_seconds >= profile.max_hold_seconds:
                reason = "time_exit"
            elif hold_seconds >= 30:
                momentum = rolling_momentum(quote, max(2, profile.lookback_samples // 2))
                if momentum is not None and momentum <= -max(
                    quote.tick_size * 0.5,
                    profile.move_trigger * REVERSAL_TRIGGER_MULT,
                ):
                    reason = "momentum_reversal"

            if reason:
                self.close_position(key, current_bid, reason)

    def close_position(self, key: tuple[str, str, str], exit_price: float, reason: str) -> None:
        position = self.stats.positions.pop(key, None)
        if position is None:
            return

        exit_price = min(1.0, max(0.0, exit_price))

        if not DRY_RUN:
            if self.live_executor is None:
                raise RuntimeError("DRY_RUN=false but live executor is not initialized.")
            try:
                response = self.live_executor.market_sell(
                    position.token_id, position.quantity
                )
                logger.info("LIVE SELL response: %s", response)
            except Exception:
                logger.exception(
                    "Live sell failed; keeping position open for retry next cycle"
                )
                self.stats.positions[key] = position
                return

        proceeds = position.quantity * exit_price
        exit_fee = taker_fee(exit_price, position.quantity)
        net_proceeds = proceeds - exit_fee
        pnl = net_proceeds - position.entry_notional - position.entry_fee
        hold_seconds = time.monotonic() - position.opened_monotonic

        self.stats.cash[position.sport][position.model] += net_proceeds
        self.stats.realized_pnl[position.sport][position.model] += pnl
        self.stats.closed_trades += 1
        self.stats.exit_reasons[reason] += 1
        if pnl > 0:
            self.stats.wins += 1
        else:
            self.stats.losses += 1

        cooldown_key = self.stats.position_key(
            position.sport, position.model, position.condition_id
        )
        self.stats.cooldowns[cooldown_key] = time.monotonic() + COOLDOWN_SECONDS

        append_trade_csv(
            action="CLOSE",
            position=position,
            price=exit_price,
            notional=proceeds,
            fee=exit_fee,
            pnl=pnl,
            reason=reason,
            hold_seconds=hold_seconds,
        )
        logger.info(
            "CLOSE %s %s %s exit=%.4f pnl=%+.4f hold=%.1fs reason=%s",
            position.sport, position.model, position.outcome,
            exit_price, pnl, hold_seconds, reason,
        )

    def close_all_positions(self, reason: str) -> None:
        for key, position in list(self.stats.positions.items()):
            quote = self.quotes.get(position.token_id)
            if quote and quote.best_bid is not None:
                exit_price = quote.best_bid
            elif quote and quote.mid is not None:
                exit_price = max(0.0, quote.mid - quote.tick_size * 0.5)
            else:
                exit_price = max(0.0, position.entry_price - 0.01)
            self.close_position(key, exit_price, reason)

    async def dashboard_loop(self) -> None:
        while not self.stop_event.is_set():
            self.render_dashboard()
            self.write_progress_line()
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), timeout=DASHBOARD_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                pass

    def write_progress_line(self) -> None:
        """Append one compact status line per interval to PROGRESS_FILE.
        Tail this file in a second Termius window for a live rolling view."""
        try:
            total_realized = sum(
                self.stats.realized_pnl[s][m] for s in SPORTS for m in MODELS
            )
            total_equity = sum(
                self.stats.equity(s, m, self.quotes) for s in SPORTS for m in MODELS
            )
            closed = self.stats.closed_trades
            wins = self.stats.wins
            win_rate = (wins / closed * 100.0) if closed else 0.0
            per_model = " ".join(
                f"{s[:3]}/{m}:{self.stats.realized_pnl[s][m]:+.2f}"
                for s in SPORTS for m in MODELS
            )
            deployed = sum(
                p.entry_notional + p.entry_fee for p in self.stats.positions.values()
            )
            line = (
                f"{utc_now_text()} equity=${total_equity:.2f}/{TOTAL_CAPITAL:.0f} "
                f"deployed=${deployed:.2f} ({deployed / TOTAL_CAPITAL * 100:.0f}%) "
                f"realized=${total_realized:+.2f} closed={closed} "
                f"open={len(self.stats.positions)} win%={win_rate:.0f} {per_model}"
            )
            with PROGRESS_FILE.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            logger.exception("Progress line write failed")

    def render_dashboard(self) -> None:
        elapsed = time.monotonic() - self.stats.started_monotonic
        remaining = max(0, int(MAX_RUNTIME_SECONDS - elapsed))
        total_closed = self.stats.closed_trades
        win_rate = self.stats.wins / total_closed * 100 if total_closed else 0.0
        live_quotes = sum(1 for q in self.quotes.values() if q.valid)
        stream_age = (
            time.monotonic() - self.stats.last_message_monotonic
            if self.stats.last_message_monotonic else float("inf")
        )

        if CLEAR_DASHBOARD:
            print("\033[2J\033[H", end="")

        print("POLYMARKET SPORTS BOT — V2 LIVE-DATA DRY RUN")
        print(
            f"UTC: {utc_now_text()} | DRY_RUN={DRY_RUN} | Remaining: {remaining}s | "
            f"WS={'LIVE' if self.ws_connected else 'DOWN'}"
        )
        age_text = "never" if not math.isfinite(stream_age) else f"{stream_age:.1f}s"
        print(
            f"Assets: {len(self.registry)} | Valid quotes: {live_quotes} | "
            f"Messages: {self.stats.messages_received} | Quote updates: {self.stats.quote_updates} | "
            f"Stream age: {age_text}"
        )
        print()
        print(
            f"{'Sport':<10} | {'Model':<6} | {'Cash':>8} | {'Realized':>9} | "
            f"{'Unrealized':>10} | {'Equity':>8} | {'Open':>4}"
        )
        print("-" * 82)

        for sport in SPORTS:
            for model in MODELS:
                cash = self.stats.cash[sport][model]
                realized = self.stats.realized_pnl[sport][model]
                unrealized = self.stats.unrealized_pnl(sport, model, self.quotes)
                equity = self.stats.equity(sport, model, self.quotes)
                open_count = self.stats.open_count(sport, model)
                print(
                    f"{sport:<10} | {model:<6} | ${cash:>7.2f} | ${realized:>+8.2f} | "
                    f"${unrealized:>+9.2f} | ${equity:>7.2f} | {open_count:>4}"
                )
            print()

        print(
            f"Entries: {self.stats.entries} | Closed: {total_closed} | Wins: {self.stats.wins} | "
            f"Losses: {self.stats.losses} | Win rate: {win_rate:.1f}% | "
            f"Open positions: {len(self.stats.positions)}"
        )
        if self.stats.exit_reasons:
            print("Exit reasons:", ", ".join(f"{k}={v}" for k, v in self.stats.exit_reasons.most_common()))
        if self.stats.positions:
            print("\nOpen positions:")
            for position in list(self.stats.positions.values())[:8]:
                quote = self.quotes.get(position.token_id)
                bid = quote.best_bid if quote and quote.best_bid is not None else position.entry_price
                pnl = (
                    position.quantity * (bid - position.entry_price)
                    - position.entry_fee
                    - taker_fee(bid, position.quantity)
                )
                print(
                    f"  {position.sport:<8} {position.model:<5} {position.outcome[:12]:<12} "
                    f"qty={position.quantity:.2f} entry={position.entry_price:.3f} "
                    f"bid={bid:.3f} uPnL={pnl:+.3f} {position.question[:48]}"
                )
        if self.last_error:
            print(f"\nLast recoverable error: {self.last_error[:160]}")
        print(f"\nTrades CSV: {TRADE_CSV.resolve()}")
        print(f"Log file:   {LOG_FILE.resolve()}")
        print("Bootstrap live-quote trades:", "ENABLED" if BOOTSTRAP_TRADES else "DISABLED")
        sys.stdout.flush()

    async def runtime_timer(self) -> None:
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=MAX_RUNTIME_SECONDS)
        except asyncio.TimeoutError:
            logger.info("Maximum runtime reached")
            self.stop_event.set()

    async def run(self) -> None:
        self.live_executor = None
        if not DRY_RUN:
            missing = []
            if LIVE_CONFIRM != "I_UNDERSTAND_LIVE_RISK":
                missing.append("LIVE_CONFIRM=I_UNDERSTAND_LIVE_RISK")
            if not POLYMARKET_PRIVATE_KEY:
                missing.append("POLYMARKET_PRIVATE_KEY")
            if not POLYMARKET_FUNDER:
                missing.append("POLYMARKET_FUNDER")
            if BOOTSTRAP_TRADES:
                raise RuntimeError(
                    "Refusing to go live with BOOTSTRAP_TRADES=true. Bootstrap "
                    "entries are forced demo trades and will spend real money on "
                    "signals that did not fire. Set BOOTSTRAP_TRADES=false."
                )
            if missing:
                raise RuntimeError(
                    "DRY_RUN=false requires deliberate setup. Missing: "
                    + ", ".join(missing)
                    + ". Leave DRY_RUN=true until the dry run has proven the strategy."
                )
            self.live_executor = LiveExecutor()

        initialize_trade_csv()
        await self.discover_markets()
        if self.stop_event.is_set():
            return

        tasks = [
            asyncio.create_task(self.websocket_loop(), name="websocket"),
            asyncio.create_task(self.strategy_loop(), name="strategy"),
            asyncio.create_task(self.dashboard_loop(), name="dashboard"),
            asyncio.create_task(self.runtime_timer(), name="runtime-timer"),
        ]

        try:
            await self.stop_event.wait()
        finally:
            self.close_all_positions("runtime_shutdown")
            self.render_dashboard()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.http.close()
            logger.info("Bot shutdown complete")


# ============================================================
# ENTRY POINT
# ============================================================
def run_self_test() -> None:
    # Every profile must be asymmetric (TP > SL) and clear the round-trip fee at p=0.50.
    worst_round_trip = 2 * taker_fee(0.50, 1.0)
    for sport in SPORTS:
        for model in MODELS:
            prof = get_profile(sport, model)
            assert prof.take_profit > prof.stop_loss, (sport, model, "TP must exceed SL")
            assert prof.take_profit > worst_round_trip, (sport, model, "TP must clear fees")
    assert abs(taker_fee(0.50, 100.0) - SPORTS_FEE_RATE * 0.25 * 100.0) < 1e-9
    assert taker_fee(0.95, 100.0) < taker_fee(0.50, 100.0)  # fee falls at extremes
    assert get_profile("Tennis", "risky").max_spread > get_profile("Tennis", "base").max_spread
    assert parse_json_list('["a", "b"]') == ["a", "b"]
    assert classify_sport("Will the MLB team win?") == "Baseball"
    assert classify_sport("ATP tennis match") == "Tennis"
    assert normalize_messages({"event_type": "book"})[0]["event_type"] == "book"

    quote = QuoteState(best_bid=0.49, best_ask=0.51)
    now = time.monotonic()
    for index, mid in enumerate((0.50, 0.505, 0.510, 0.515, 0.520, 0.525)):
        quote.mids.append((now + index, mid))
    assert rolling_momentum(quote, 4) is not None
    print("Self-test passed.")


async def async_main() -> None:
    bot = SportsTradingBot()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, bot.stop_event.set)
        except NotImplementedError:
            pass

    await bot.run()


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        run_self_test()
    else:
        try:
            asyncio.run(async_main())
        except KeyboardInterrupt:
            pass
