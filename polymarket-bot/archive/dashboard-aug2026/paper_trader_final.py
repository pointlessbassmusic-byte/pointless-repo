#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import csv
import json
import math
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests
import websockets
from sharp_oracle import SharpOracle

DASHBOARD_METRICS = Path("/root/sim_metrics.csv")
METRICS_HISTORY = Path("/root/paper_metrics_history.csv")
TRADE_LOG = Path("/root/paper_trades.csv")

DASHBOARD_HEADER = (
    "timestamp,Tennis,Baseball,Table_Tennis,"
    "considered,taken,closed,cumulative_pnl,avg_profit_per_trade"
)

VIRTUAL_CAPITAL = {"Tennis": 150.0, "Baseball": 150.0, "Table Tennis": 150.0}
TRADING_ENABLED = {"Tennis": True, "Baseball": False, "Table Tennis": False}

METRICS = {
    sport: {"considered": 0, "taken": 0, "closed": 0, "pnl": 0.0}
    for sport in VIRTUAL_CAPITAL
}

ACTIVE_TRADES: dict[str, dict[str, Any]] = {}
MARKET_REGISTRY: dict[str, dict[str, Any]] = {}
BOOKS: dict[str, dict[str, float]] = {}
ORACLE_CACHE: dict[str, dict[str, Any]] = {}
REALIZED_PNLS: list[float] = []
SUBSCRIBED: set[str] = set()
ACTIVE_WEBSOCKET = None
TRADING_HALTED_REASON = ""

TRADE_NOTIONAL_USD = 15.00

# Exit rules are based on the position's net executable liquidation value
# relative to its initial cash outlay (trade notional + modeled entry fee).
MIN_TAKE_PROFIT_MULTIPLE = 1.09
MAX_TAKE_PROFIT_MULTIPLE = 20.00
STOP_LOSS_MULTIPLE = 0.88

MAX_HOLD_SECONDS = 900

# 20x is only mathematically possible for sufficiently low entry prices on a
# binary $0-$1 token, so the research floor is lowered from $0.10 to $0.01.
MIN_ENTRY_PRICE = 0.01
MAX_ENTRY_PRICE = 0.90
MAX_SPREAD = 0.015
MAX_OPEN_TENNIS_POSITIONS = 1

MIN_RAW_EDGE = 0.025
MIN_NET_EDGE = 0.008
MIN_EDGE_MULTIPLE = 1.35

DEFAULT_SPORTS_TAKER_FEE_RATE = 0.03

ORACLE_CACHE_SECONDS = 10
FUZZY_MATCH_THRESHOLD = 0.92
FUZZY_UNIQUENESS_MARGIN = 0.05

MAX_SESSION_LOSS = 15.00
ROLLING_GATE_MIN_TRADES = 20
ROLLING_GATE_WINDOW = 20
ROLLING_GATE_MIN_PROFIT_FACTOR = 1.00
ROLLING_GATE_MIN_AVG_PNL = 0.0

MARKET_REFRESH_SECONDS = 60
REST_EXIT_POLL_SECONDS = 5
WS_HEARTBEAT_SECONDS = 10
WS_SUBSCRIBE_CHUNK = 200

SHARP_KEY = os.getenv("SHARP_ORACLE_API_KEY", "").strip()
if not SHARP_KEY:
    raise RuntimeError("SHARP_ORACLE_API_KEY is missing.")
oracle = SharpOracle(api_key=SHARP_KEY)

BANNED_MARKET_TEXT = (
    "completed-match",
    "completed match",
    "set winner",
    "first set",
    "second set",
    "third set",
    "set-1",
    "set-2",
    "set-3",
    "set 1",
    "set 2",
    "set 3",
    "match total",
    "match-total",
    "total games",
    "over ",
    "under ",
    "handicap",
    "game winner",
    "game-winner",
    "correct score",
    "to win tournament",
    "tournament winner",
    "reach the",
    "advance to",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(text)
    except Exception:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9 ]+", " ", text).lower()
    return " ".join(text.split())


def probability(value: Any) -> float | None:
    try:
        p = float(value)
    except Exception:
        return None
    if p > 1.0:
        if p <= 1.001:
            return None
        p = 1.0 / p
    if not 0.01 < p < 0.99:
        return None
    return p


def fee_per_share(price: float, fee_rate: float) -> float:
    p = min(max(float(price), 0.0001), 0.9999)
    return float(fee_rate) * p * (1.0 - p)


def net_liquidation_value(
    shares: float,
    price: float,
    fee_rate: float,
) -> float:
    px = min(max(float(price), 0.0001), 0.9999)
    exit_fee = shares * fee_per_share(px, fee_rate)
    return shares * px - exit_fee


def liquidation_multiple(
    shares: float,
    price: float,
    fee_rate: float,
    initial_cash_outlay: float,
) -> float:
    if initial_cash_outlay <= 0:
        return 0.0
    return (
        net_liquidation_value(shares, price, fee_rate)
        / initial_cash_outlay
    )


def profit_factor(values: list[float]) -> float:
    gp = sum(max(0.0, x) for x in values)
    gl = -sum(min(0.0, x) for x in values)
    if gl > 0:
        return gp / gl
    return math.inf if gp > 0 else 0.0


def session_halted() -> bool:
    global TRADING_HALTED_REASON
    if METRICS["Tennis"]["pnl"] <= -MAX_SESSION_LOSS:
        TRADING_HALTED_REASON = "SESSION_LOSS_LIMIT"
        return True

    if len(REALIZED_PNLS) >= ROLLING_GATE_MIN_TRADES:
        window = REALIZED_PNLS[-ROLLING_GATE_WINDOW:]
        avg = sum(window) / len(window)
        pf = profit_factor(window)
        if avg <= ROLLING_GATE_MIN_AVG_PNL:
            TRADING_HALTED_REASON = "ROLLING_AVG_NONPOSITIVE"
            return True
        if pf < ROLLING_GATE_MIN_PROFIT_FACTOR:
            TRADING_HALTED_REASON = "ROLLING_PROFIT_FACTOR"
            return True

    return False


def exact_dashboard_row() -> str:
    total_considered = sum(m["considered"] for m in METRICS.values())
    total_taken = sum(m["taken"] for m in METRICS.values())
    total_closed = sum(m["closed"] for m in METRICS.values())
    total_pnl = sum(m["pnl"] for m in METRICS.values())
    avg_profit = total_pnl / total_closed if total_closed else 0.0

    return (
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},"
        f"{VIRTUAL_CAPITAL['Tennis']:.2f},"
        f"{VIRTUAL_CAPITAL['Baseball']:.2f},"
        f"{VIRTUAL_CAPITAL['Table Tennis']:.2f},"
        f"{total_considered},"
        f"{total_taken},"
        f"{total_closed},"
        f"{total_pnl:.4f},"
        f"{avg_profit:.4f}"
    )


def write_dashboard_atomic() -> None:
    tmp = DASHBOARD_METRICS.with_suffix(".csv.tmp")
    row = exact_dashboard_row()
    tmp.write_text(DASHBOARD_HEADER + "\n" + row + "\n", encoding="utf-8")
    os.replace(tmp, DASHBOARD_METRICS)

    exists = METRICS_HISTORY.exists()
    with METRICS_HISTORY.open("a", encoding="utf-8") as handle:
        if not exists:
            handle.write(DASHBOARD_HEADER + "\n")
        handle.write(row + "\n")


def append_trade(row: dict[str, Any]) -> None:
    fields = [
        "timestamp", "sport", "market_key", "player", "token_id",
        "entry_price", "exit_price", "shares", "entry_fee", "exit_fee",
        "gross_pnl", "net_pnl", "reason", "anchor_entry", "anchor_exit",
        "spread_entry", "hold_seconds",
        "target_multiple", "exit_multiple",
    ]
    exists = TRADE_LOG.exists()
    with TRADE_LOG.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def is_pre_match_tennis_h2h(event: dict[str, Any], market: dict[str, Any]) -> bool:
    tokens = parse_list(market.get("clobTokenIds") or market.get("clob_token_ids"))
    outcomes = parse_list(market.get("outcomes"))

    if len(tokens) != 2 or len(outcomes) != 2:
        return False

    normalized = {normalize_name(outcome) for outcome in outcomes}
    if normalized <= {"yes", "no"}:
        return False

    text = " ".join(
        [
            str(event.get("slug") or ""),
            str(event.get("title") or ""),
            str(market.get("slug") or ""),
            str(market.get("question") or ""),
            str(market.get("groupItemTitle") or ""),
            str(market.get("sportsMarketType") or ""),
        ]
    ).lower()

    if any(term in text for term in BANNED_MARKET_TEXT):
        return False

    start = parse_time(
        market.get("eventStartTime")
        or market.get("gameStartTime")
        or market.get("startDate")
        or event.get("startDate")
        or event.get("startTime")
    )
    if start is not None and start <= utc_now():
        return False

    return True


def market_fee_rate(market: dict[str, Any]) -> float:
    if market.get("feesEnabled") is False:
        return 0.0

    schedule = market.get("feeSchedule") or market.get("fee_schedule") or {}
    if isinstance(schedule, str):
        try:
            schedule = json.loads(schedule)
        except Exception:
            schedule = {}

    try:
        value = float(schedule.get("rate"))
        if value >= 0:
            return value
    except Exception:
        pass

    return DEFAULT_SPORTS_TAKER_FEE_RATE


def fetch_registry_sync() -> dict[str, dict[str, Any]]:
    response = requests.get(
        "https://gamma-api.polymarket.com/events",
        params={
            "active": "true",
            "closed": "false",
            "tag_slug": "tennis",
            "limit": 500,
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    response.raise_for_status()

    new_registry: dict[str, dict[str, Any]] = {}
    accepted = 0
    rejected = 0

    for event in response.json():
        for market in event.get("markets", []):
            if not is_pre_match_tennis_h2h(event, market):
                rejected += 1
                continue

            tokens = [str(x) for x in parse_list(
                market.get("clobTokenIds") or market.get("clob_token_ids")
            )]
            outcomes = [str(x) for x in parse_list(market.get("outcomes"))]
            if len(tokens) != 2 or len(outcomes) != 2:
                rejected += 1
                continue

            accepted += 1
            market_key = str(
                market.get("conditionId")
                or market.get("condition_id")
                or market.get("id")
                or market.get("slug")
                or event.get("slug")
                or ""
            )
            fee_rate = market_fee_rate(market)
            slug = str(market.get("slug") or event.get("slug") or "")
            question = str(market.get("question") or event.get("title") or "")

            for index, token_id in enumerate(tokens):
                sibling_index = 1 - index
                new_registry[token_id] = {
                    "sport": "Tennis",
                    "market_key": market_key,
                    "slug": slug,
                    "question": question,
                    "outcome": outcomes[index],
                    "sibling_token": tokens[sibling_index],
                    "sibling_outcome": outcomes[sibling_index],
                    "fee_rate": fee_rate,
                }

    print(
        f"[MARKETS] accepted_h2h={accepted} "
        f"rejected_non_h2h={rejected} tokens={len(new_registry)}"
    )
    return new_registry


async def refresh_registry() -> tuple[set[str], set[str]]:
    global MARKET_REGISTRY
    old = set(MARKET_REGISTRY)
    new_registry = await asyncio.to_thread(fetch_registry_sync)
    if not new_registry:
        return set(), set()
    MARKET_REGISTRY = new_registry
    new = set(new_registry)
    return new - old, old - new


def oracle_lookup_sync(player: str) -> tuple[float | None, str]:
    direct = probability(oracle.get_anchor(player))
    if direct is not None:
        return direct, "exact"

    cache = getattr(oracle, "cache", {})
    if not isinstance(cache, dict) or not cache:
        return None, "missing"

    target = normalize_name(player)
    candidates: list[tuple[float, str, float]] = []

    for cached_name, raw_prob in cache.items():
        p = probability(raw_prob)
        if p is None:
            continue
        score = SequenceMatcher(
            None,
            target,
            normalize_name(str(cached_name)),
        ).ratio()
        candidates.append((score, str(cached_name), p))

    if not candidates:
        return None, "missing"

    candidates.sort(reverse=True, key=lambda row: row[0])
    best_score, best_name, best_prob = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0.0

    if (
        best_score >= FUZZY_MATCH_THRESHOLD
        and best_score - second_score >= FUZZY_UNIQUENESS_MARGIN
    ):
        print(
            f"[ORACLE FUZZY] '{player}' -> '{best_name}' "
            f"score={best_score:.3f}"
        )
        return best_prob, "fuzzy"

    return None, "missing"


async def get_anchor(player: str) -> float | None:
    now = time.monotonic()
    cached = ORACLE_CACHE.get(player)
    if cached and now - cached["time"] <= ORACLE_CACHE_SECONDS:
        return cached["value"]

    try:
        value, mode = await asyncio.to_thread(oracle_lookup_sync, player)
    except Exception as exc:
        print(f"[ORACLE ERROR] {player}: {type(exc).__name__}: {exc}")
        value = None
        mode = "error"

    ORACLE_CACHE[player] = {"time": now, "value": value, "mode": mode}
    return value


def market_has_open_position(market_key: str) -> bool:
    return any(
        trade["market_key"] == market_key
        for trade in ACTIVE_TRADES.values()
    )


def close_position(
    token_id: str,
    exit_bid: float,
    reason: str,
    anchor_exit: float | None,
) -> None:
    trade = ACTIVE_TRADES.get(token_id)
    if trade is None:
        return

    exit_bid = min(max(float(exit_bid), 0.0001), 0.9999)
    shares = trade["shares"]
    fee_rate = trade["fee_rate"]
    exit_fee = shares * fee_per_share(exit_bid, fee_rate)
    gross_pnl = shares * (exit_bid - trade["entry_price"])
    net_pnl = gross_pnl - trade["entry_fee"] - exit_fee

    exit_multiple = liquidation_multiple(
        shares,
        exit_bid,
        fee_rate,
        trade["initial_cash_outlay"],
    )

    sport = trade["sport"]
    METRICS[sport]["closed"] += 1
    METRICS[sport]["pnl"] += net_pnl
    VIRTUAL_CAPITAL[sport] += net_pnl
    REALIZED_PNLS.append(net_pnl)

    hold = time.monotonic() - trade["opened_monotonic"]

    append_trade(
        {
            "timestamp": utc_now().isoformat(),
            "sport": sport,
            "market_key": trade["market_key"],
            "player": trade["player"],
            "token_id": token_id,
            "entry_price": f"{trade['entry_price']:.6f}",
            "exit_price": f"{exit_bid:.6f}",
            "shares": f"{shares:.6f}",
            "entry_fee": f"{trade['entry_fee']:.6f}",
            "exit_fee": f"{exit_fee:.6f}",
            "gross_pnl": f"{gross_pnl:.6f}",
            "net_pnl": f"{net_pnl:.6f}",
            "reason": reason,
            "anchor_entry": f"{trade['anchor_entry']:.6f}",
            "anchor_exit": "" if anchor_exit is None else f"{anchor_exit:.6f}",
            "spread_entry": f"{trade['spread_entry']:.6f}",
            "hold_seconds": f"{hold:.1f}",
            "target_multiple": f"{trade['target_multiple']:.6f}",
            "exit_multiple": f"{exit_multiple:.6f}",
        }
    )

    print(
        f"[CLOSE] {trade['player']} reason={reason} "
        f"entry={trade['entry_price']:.3f} bid={exit_bid:.3f} "
        f"multiple={exit_multiple:.3f}x "
        f"target={trade['target_multiple']:.3f}x "
        f"net=${net_pnl:+.3f} balance=${VIRTUAL_CAPITAL[sport]:.2f}"
    )

    del ACTIVE_TRADES[token_id]


async def evaluate_position(token_id: str, bid: float) -> None:
    trade = ACTIVE_TRADES.get(token_id)
    if trade is None:
        return

    anchor = await get_anchor(trade["player"])
    age = time.monotonic() - trade["opened_monotonic"]

    current_multiple = liquidation_multiple(
        trade["shares"],
        bid,
        trade["fee_rate"],
        trade["initial_cash_outlay"],
    )

    # Profit exit: dynamically selected at entry from SharpOracle-supported
    # upside, never below 1.09x and never above 20x.
    if current_multiple >= trade["target_multiple"]:
        close_position(token_id, bid, "TAKE_PROFIT_MULTIPLE", anchor)
        return

    # Stop exit: sell when net executable liquidation value reaches 88% of the
    # initial cash outlay. Fast price gaps can still realize below 0.88x.
    if current_multiple <= STOP_LOSS_MULTIPLE:
        close_position(token_id, bid, "STOP_LOSS_12_PERCENT", anchor)
        return

    # Oracle invalidation is a risk exit, not a take-profit event. It may close
    # above or below 1.00x because the original edge no longer exists.
    if anchor is not None and anchor <= trade["entry_price"]:
        close_position(token_id, bid, "ANCHOR_INVALIDATED", anchor)
        return

    if age >= MAX_HOLD_SECONDS:
        close_position(token_id, bid, "MAX_HOLD", anchor)


async def consider_entry(token_id: str, bid: float, ask: float) -> None:
    meta = MARKET_REGISTRY.get(token_id)
    if meta is None:
        return

    sport = meta["sport"]
    METRICS[sport]["considered"] += 1

    if not TRADING_ENABLED.get(sport, False):
        return
    if session_halted():
        return
    if token_id in ACTIVE_TRADES:
        return
    if market_has_open_position(meta["market_key"]):
        return
    if sum(1 for t in ACTIVE_TRADES.values() if t["sport"] == "Tennis") >= MAX_OPEN_TENNIS_POSITIONS:
        return

    spread = ask - bid
    if spread <= 0 or spread > MAX_SPREAD:
        return
    if not MIN_ENTRY_PRICE <= ask <= MAX_ENTRY_PRICE:
        return

    anchor = await get_anchor(meta["outcome"])
    if anchor is None:
        return

    raw_edge = anchor - ask
    if raw_edge < MIN_RAW_EDGE:
        return

    shares = TRADE_NOTIONAL_USD / ask
    entry_fee_ps = fee_per_share(ask, meta["fee_rate"])
    entry_fee = shares * entry_fee_ps
    initial_cash_outlay = TRADE_NOTIONAL_USD + entry_fee

    # Determine how much upside the sharp anchor actually supports after a
    # modeled exit fee. A trade is rejected unless that support reaches the
    # user's minimum 1.09x take-profit multiple.
    anchor_multiple = liquidation_multiple(
        shares,
        anchor,
        meta["fee_rate"],
        initial_cash_outlay,
    )

    if anchor_multiple < MIN_TAKE_PROFIT_MULTIPLE:
        return

    # A binary token cannot liquidate above $1. Cap the target by both 20x and
    # the maximum economically achievable multiple for this exact entry.
    max_achievable_multiple = liquidation_multiple(
        shares,
        0.9999,
        meta["fee_rate"],
        initial_cash_outlay,
    )

    target_multiple = min(
        MAX_TAKE_PROFIT_MULTIPLE,
        anchor_multiple,
        max_achievable_multiple,
    )

    if target_multiple < MIN_TAKE_PROFIT_MULTIPLE:
        return

    # Cost-aware edge gate remains in force.
    projected_exit_price = min(anchor, 0.9999)
    exit_fee_ps = fee_per_share(projected_exit_price, meta["fee_rate"])
    estimated_cost_ps = entry_fee_ps + exit_fee_ps + 0.50 * spread

    net_edge = raw_edge - estimated_cost_ps
    edge_multiple = raw_edge / max(estimated_cost_ps, 1e-9)

    if net_edge < MIN_NET_EDGE:
        return
    if edge_multiple < MIN_EDGE_MULTIPLE:
        return

    ACTIVE_TRADES[token_id] = {
        "sport": sport,
        "market_key": meta["market_key"],
        "player": meta["outcome"],
        "entry_price": ask,
        "shares": shares,
        "entry_fee": entry_fee,
        "initial_cash_outlay": initial_cash_outlay,
        "fee_rate": meta["fee_rate"],
        "anchor_entry": anchor,
        "anchor_multiple": anchor_multiple,
        "target_multiple": target_multiple,
        "spread_entry": spread,
        "opened_monotonic": time.monotonic(),
    }
    METRICS[sport]["taken"] += 1

    print(
        f"[BUY] {meta['outcome']} ask={ask:.3f} bid={bid:.3f} "
        f"anchor={anchor:.3f} raw_edge={raw_edge:.3f} "
        f"net_edge={net_edge:.3f} edge_mult={edge_multiple:.2f} "
        f"tp_target={target_multiple:.3f}x "
        f"anchor_support={anchor_multiple:.3f}x "
        f"stop={STOP_LOSS_MULTIPLE:.2f}x spread={spread:.3f}"
    )


async def process_quote(token_id: str, bid: Any, ask: Any) -> None:
    if token_id not in MARKET_REGISTRY:
        return

    try:
        bid_f = float(bid)
        ask_f = float(ask)
    except Exception:
        return

    if not (0.0 < bid_f < ask_f < 1.0):
        return

    BOOKS[token_id] = {
        "bid": bid_f,
        "ask": ask_f,
        "updated": time.monotonic(),
    }

    if token_id in ACTIVE_TRADES:
        await evaluate_position(token_id, bid_f)
    else:
        await consider_entry(token_id, bid_f, ask_f)


async def process_event(event: dict[str, Any]) -> None:
    if not isinstance(event, dict):
        return

    event_type = event.get("event_type")

    if event_type == "book":
        token_id = str(event.get("asset_id") or "")
        bids = event.get("bids") or []
        asks = event.get("asks") or []
        try:
            bid = max(float(level["price"]) for level in bids)
            ask = min(float(level["price"]) for level in asks)
        except Exception:
            return
        await process_quote(token_id, bid, ask)
        return

    if event_type == "best_bid_ask":
        await process_quote(
            str(event.get("asset_id") or ""),
            event.get("best_bid"),
            event.get("best_ask"),
        )
        return

    if event_type == "price_change":
        for change in event.get("price_changes") or []:
            bid = change.get("best_bid")
            ask = change.get("best_ask")
            if bid is not None and ask is not None:
                await process_quote(
                    str(change.get("asset_id") or ""),
                    bid,
                    ask,
                )
        return

    if event_type == "market_resolved":
        winning = str(event.get("winning_asset_id") or "")
        for token_id in event.get("assets_ids") or []:
            token_id = str(token_id)
            if token_id in ACTIVE_TRADES:
                settlement = 1.0 if token_id == winning else 0.0001
                close_position(token_id, settlement, "MARKET_RESOLVED", None)


async def heartbeat(ws) -> None:
    while True:
        await asyncio.sleep(WS_HEARTBEAT_SECONDS)
        await ws.send("PING")


async def subscribe_chunks(ws, token_ids: list[str], initial: bool) -> None:
    global SUBSCRIBED

    for start in range(0, len(token_ids), WS_SUBSCRIBE_CHUNK):
        chunk = token_ids[start:start + WS_SUBSCRIBE_CHUNK]
        if not chunk:
            continue

        if initial and start == 0:
            payload = {
                "type": "market",
                "assets_ids": chunk,
                "custom_feature_enabled": True,
            }
        else:
            payload = {
                "operation": "subscribe",
                "assets_ids": chunk,
                "custom_feature_enabled": True,
            }

        await ws.send(json.dumps(payload))
        SUBSCRIBED.update(chunk)


async def sync_subscriptions(ws) -> None:
    global SUBSCRIBED

    wanted = set(MARKET_REGISTRY)
    add = sorted(wanted - SUBSCRIBED)
    remove = sorted(SUBSCRIBED - wanted)

    if add:
        await subscribe_chunks(ws, add, initial=False)

    for start in range(0, len(remove), WS_SUBSCRIBE_CHUNK):
        chunk = remove[start:start + WS_SUBSCRIBE_CHUNK]
        if chunk:
            await ws.send(
                json.dumps(
                    {
                        "operation": "unsubscribe",
                        "assets_ids": chunk,
                    }
                )
            )
            SUBSCRIBED.difference_update(chunk)


async def stream_websocket() -> None:
    global ACTIVE_WEBSOCKET, SUBSCRIBED
    url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    while True:
        if not MARKET_REGISTRY:
            await asyncio.sleep(3)
            continue

        try:
            SUBSCRIBED = set()
            async with websockets.connect(
                url,
                ping_interval=None,
                close_timeout=5,
                max_queue=4096,
            ) as ws:
                ACTIVE_WEBSOCKET = ws
                await subscribe_chunks(ws, sorted(MARKET_REGISTRY), initial=True)
                print(f"[WS] connected subscribed={len(SUBSCRIBED)}")

                heartbeat_task = asyncio.create_task(heartbeat(ws))
                try:
                    while True:
                        raw = await ws.recv()
                        if raw == "PONG":
                            continue
                        try:
                            data = json.loads(raw)
                        except Exception:
                            continue

                        if isinstance(data, list):
                            for event in data:
                                await process_event(event)
                        elif isinstance(data, dict):
                            await process_event(data)
                finally:
                    heartbeat_task.cancel()

        except Exception as exc:
            ACTIVE_WEBSOCKET = None
            print(
                f"[WS ERROR] {type(exc).__name__}: {exc}; reconnecting in 5s"
            )
            await asyncio.sleep(5)


async def market_refresh_loop() -> None:
    while True:
        await asyncio.sleep(MARKET_REFRESH_SECONDS)
        try:
            await refresh_registry()
            if ACTIVE_WEBSOCKET is not None:
                await sync_subscriptions(ACTIVE_WEBSOCKET)
        except Exception as exc:
            print(f"[MARKET REFRESH ERROR] {type(exc).__name__}: {exc}")


def fetch_book_sync(token_id: str) -> tuple[float, float] | None:
    response = requests.get(
        "https://clob.polymarket.com/book",
        params={"token_id": token_id},
        timeout=5,
    )
    response.raise_for_status()
    data = response.json()
    bids = data.get("bids") or []
    asks = data.get("asks") or []
    if not bids or not asks:
        return None
    bid = max(float(level["price"]) for level in bids)
    ask = min(float(level["price"]) for level in asks)
    if not 0 < bid < ask < 1:
        return None
    return bid, ask


async def rest_exit_fallback() -> None:
    while True:
        await asyncio.sleep(REST_EXIT_POLL_SECONDS)

        for token_id in list(ACTIVE_TRADES):
            try:
                result = await asyncio.to_thread(fetch_book_sync, token_id)
                if result is not None:
                    bid, ask = result
                    await process_quote(token_id, bid, ask)
            except Exception:
                continue


async def log_metrics() -> None:
    while True:
        await asyncio.sleep(2)
        write_dashboard_atomic()

        total_considered = sum(m["considered"] for m in METRICS.values())
        total_taken = sum(m["taken"] for m in METRICS.values())
        total_closed = sum(m["closed"] for m in METRICS.values())
        total_pnl = sum(m["pnl"] for m in METRICS.values())

        print(
            f"[TELEMETRY] considered={total_considered} taken={total_taken} "
            f"closed={total_closed} pnl=${total_pnl:+.2f} "
            f"open={len(ACTIVE_TRADES)} "
            f"halt={TRADING_HALTED_REASON or 'clear'}"
        )


async def main() -> None:
    write_dashboard_atomic()
    await refresh_registry()

    if not MARKET_REGISTRY:
        raise RuntimeError("No valid pre-match Tennis H2H markets found.")

    await asyncio.gather(
        stream_websocket(),
        rest_exit_fallback(),
        market_refresh_loop(),
        log_metrics(),
    )


if __name__ == "__main__":
    asyncio.run(main())
