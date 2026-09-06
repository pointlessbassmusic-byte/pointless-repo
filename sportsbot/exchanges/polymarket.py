"""Polymarket client (CLOB V2 era, 2026).

Two transport layers:

* **Reads** (market discovery, order books) go through the public Gamma API
  (https://gamma-api.polymarket.com) and CLOB read endpoints via plain
  ``httpx`` — no auth, works everywhere, no heavy dependencies.
* **Trading** goes through the official unified py-sdk (PyPI
  ``polymarket-client``); the archived ``py-clob-client`` targets the retired
  V1 contracts and must not be used. The SDK is imported lazily so the rest
  of the system (paper mode, scanning, backtesting) runs without it.

Sports moneyline markets are binary CLOB markets whose outcomes are the two
team/player names (not Yes/No). We map ``outcomes[0]`` -> the market's YES
side: ``MarketInfo.home = outcomes[0]``, ``yes_token_id = clobTokenIds[0]``,
and predictions are P(outcomes[0] wins).

IMPORTANT (compliance): order placement on the main CLOB is geoblocked for
US IPs and ~30 other jurisdictions (reads are open). This client does not and
must not attempt to circumvent that; if orders fail from a blocked region the
correct paths are paper mode, Polymarket US (separate regulated API), or
Kalshi (see kalshi.py).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from sportsbot.core.types import (
    BookLevel,
    Exchange,
    MarketInfo,
    MarketQuote,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Sport,
)
from sportsbot.exchanges.base import ExchangeClient

log = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

# Live-verified Gamma tag ids (Sep 2026).
SPORT_TAGS: dict[str, int] = {
    "sports": 1,
    "tennis": 864,
    "atp": 101232,
    "mlb": 100381,
    "baseball": 100381,   # alias: config sport keys use "baseball"
    "table_tennis": 103767,
}

SPORT_FOR_TAG = {
    "tennis": Sport.TENNIS,
    "atp": Sport.TENNIS,
    "mlb": Sport.BASEBALL,
    "baseball": Sport.BASEBALL,
    "table_tennis": Sport.TABLE_TENNIS,
}


def _parse_json_field(value: Any) -> list:
    """Gamma returns clobTokenIds/outcomes/outcomePrices as JSON-encoded strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def taker_fee(price: float, shares: float, base_fee_bps: float = 1000.0) -> float:
    """Estimated taker fee in dollars: rate × min(p, 1-p) × shares.

    With the observed sports taker_base_fee of 1000 bps this peaks at 5% of
    notional at p=0.5 and falls toward the extremes. Makers pay 0. Verify
    realized fees on a small live trade before scaling (the docs formula and
    the bps parametrization differ in form).
    """
    rate = base_fee_bps / 10000.0
    return rate * min(price, 1.0 - price) * shares


class PolymarketClient(ExchangeClient):
    exchange = Exchange.POLYMARKET

    def __init__(
        self,
        private_key: Optional[str] = None,
        funder: Optional[str] = None,
        signature_type: int = 0,
        timeout: float = 15.0,
    ) -> None:
        self.private_key = private_key or os.environ.get("POLYMARKET_PRIVATE_KEY") or None
        self.funder = funder or os.environ.get("POLYMARKET_FUNDER") or None
        sig_env = os.environ.get("POLYMARKET_SIGNATURE_TYPE")
        self.signature_type = int(sig_env) if sig_env else signature_type
        self.http = httpx.Client(timeout=timeout, headers={"User-Agent": "sportsbot/1.0"})
        self._sdk = None  # lazily created SecureClient

    # ------------------------------------------------------------------
    # Discovery (Gamma, public)
    # ------------------------------------------------------------------
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=8), reraise=True)
    def _get(self, url: str, params: dict | None = None) -> Any:
        resp = self.http.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def list_sports_markets(self, sport_tag: str) -> list[MarketInfo]:
        """Active moneyline markets for a sport tag ('tennis'|'mlb'|'table_tennis')."""
        tag_id = SPORT_TAGS.get(sport_tag)
        if tag_id is None:
            raise ValueError(f"unknown sport tag {sport_tag!r}; known: {sorted(SPORT_TAGS)}")
        sport = SPORT_FOR_TAG.get(sport_tag)

        out: list[MarketInfo] = []
        offset = 0
        page_size = 100
        # Some long-stale events stay active=true; ask only for events starting
        # from yesterday onward (yesterday, not today, to keep live matches).
        start_min = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        while True:
            events = self._get(
                f"{GAMMA_BASE}/events",
                params={
                    "tag_id": tag_id,
                    "active": "true",
                    "closed": "false",
                    "start_date_min": start_min,
                    "order": "startDate",
                    "ascending": "true",
                    "limit": page_size,
                    "offset": offset,
                },
            )
            if not isinstance(events, list) or not events:
                break
            for ev in events:
                out.extend(self._moneyline_markets_from_event(ev, sport))
            if len(events) < page_size:
                break
            offset += page_size
            if offset >= 1000:  # sanity bound; a day's slate is far smaller
                log.warning("gamma pagination cap hit for tag %s", sport_tag)
                break
        return out

    def _moneyline_markets_from_event(self, ev: dict, sport: Optional[Sport]) -> list[MarketInfo]:
        infos: list[MarketInfo] = []
        for m in ev.get("markets") or []:
            # Event-level active/closed does not guarantee tradeable markets.
            if m.get("sportsMarketType") != "moneyline":
                continue
            if m.get("closed") or not m.get("acceptingOrders", True):
                continue
            if not m.get("enableOrderBook", True):
                continue
            outcomes = _parse_json_field(m.get("outcomes"))
            tokens = _parse_json_field(m.get("clobTokenIds"))
            if len(outcomes) != 2 or len(tokens) != 2:
                continue
            infos.append(
                MarketInfo(
                    exchange=Exchange.POLYMARKET,
                    market_id=m.get("conditionId", ""),
                    yes_token_id=str(tokens[0]),
                    no_token_id=str(tokens[1]),
                    question=m.get("question", "") or ev.get("title", ""),
                    slug=m.get("slug", "") or ev.get("slug", ""),
                    sport=sport,
                    home=str(outcomes[0]),
                    away=str(outcomes[1]),
                    start_time=_parse_dt(m.get("gameStartTime") or ev.get("startDate")),
                    close_time=_parse_dt(m.get("endDate") or ev.get("endDate")),
                    active=bool(m.get("active", True)),
                    tick_size=float(m.get("orderPriceMinTickSize") or 0.01),
                    min_order_size=float(m.get("orderMinSize") or 5),
                    neg_risk=bool(m.get("negRisk", False)),
                    meta={
                        "event_slug": ev.get("slug"),
                        "game_id": m.get("gameId"),
                        "taker_base_fee": m.get("takerBaseFee"),
                        "fees_enabled": m.get("feesEnabled"),
                        "outcome_prices": _parse_json_field(m.get("outcomePrices")),
                    },
                )
            )
        return infos

    # ------------------------------------------------------------------
    # Quotes (CLOB read endpoints, public)
    # ------------------------------------------------------------------
    def get_quote(self, market: MarketInfo) -> MarketQuote:
        if not market.yes_token_id:
            raise ValueError(f"market {market.market_id} has no yes_token_id")
        book = self._get(f"{CLOB_BASE}/book", params={"token_id": market.yes_token_id})
        bids = sorted(
            (BookLevel(price=float(b["price"]), size=float(b["size"]))
             for b in book.get("bids", [])),
            key=lambda lvl: -lvl.price,
        )
        asks = sorted(
            (BookLevel(price=float(a["price"]), size=float(a["size"]))
             for a in book.get("asks", [])),
            key=lambda lvl: lvl.price,
        )
        return MarketQuote(
            market_id=market.market_id,
            bid=bids[0].price if bids else None,
            ask=asks[0].price if asks else None,
            bids=bids,
            asks=asks,
        )

    # ------------------------------------------------------------------
    # Trading (official py-sdk, lazy)
    # ------------------------------------------------------------------
    def _sdk_client(self):
        if self._sdk is None:
            if not self.private_key:
                raise RuntimeError(
                    "POLYMARKET_PRIVATE_KEY not set — trading unavailable "
                    "(reads and paper mode still work)"
                )
            try:
                from polymarket import SecureClient  # type: ignore
            except ImportError:
                try:
                    from polymarket_client import SecureClient  # type: ignore
                except ImportError as exc:  # pragma: no cover
                    raise RuntimeError(
                        "polymarket-client SDK not installed; "
                        "pip install 'sportsbot[polymarket]'"
                    ) from exc
            kwargs: dict[str, Any] = {"private_key": self.private_key}
            if self.funder:
                kwargs["wallet"] = self.funder
            self._sdk = SecureClient.create(**kwargs)
        return self._sdk

    def place_order(self, order: Order) -> Order:
        if not order.token_id:
            order.status = OrderStatus.REJECTED
            order.raw = {"error": "missing token_id"}
            return order
        sdk = self._sdk_client()
        try:
            if order.order_type in (OrderType.FOK, OrderType.IOC):
                resp = sdk.place_market_order(
                    token_id=order.token_id,
                    side="BUY",
                    amount=round(order.price * order.size, 2),
                    order_type="FOK" if order.order_type == OrderType.FOK else "FAK",
                )
            else:
                resp = sdk.place_limit_order(
                    token_id=order.token_id,
                    side="BUY",
                    price=order.price,
                    size=order.size,
                )
            data = resp if isinstance(resp, dict) else getattr(resp, "__dict__", {"resp": str(resp)})
            order.order_id = str(
                data.get("order_id") or data.get("orderID") or data.get("id") or ""
            )
            status = str(data.get("status", "live")).lower()
            order.status = OrderStatus.FILLED if status == "matched" else OrderStatus.OPEN
            order.raw = {"response": data}
        except Exception as exc:  # surface, never crash the loop
            log.error("polymarket order failed: %s", exc)
            order.status = OrderStatus.REJECTED
            order.raw = {"error": str(exc)}
        return order

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._sdk_client().cancel_order(order_id)
            return True
        except Exception as exc:
            log.error("polymarket cancel failed: %s", exc)
            return False

    def get_open_orders(self) -> list[Order]:
        orders: list[Order] = []
        try:
            for o in self._sdk_client().list_open_orders() or []:
                d = o if isinstance(o, dict) else getattr(o, "__dict__", {})
                orders.append(
                    Order(
                        order_id=str(d.get("id") or d.get("order_id") or ""),
                        exchange=Exchange.POLYMARKET,
                        market_id=str(d.get("market") or d.get("condition_id") or ""),
                        token_id=str(d.get("asset_id") or d.get("token_id") or ""),
                        side=Side.YES,
                        price=float(d.get("price") or 0),
                        size=float(d.get("original_size") or d.get("size") or 0),
                        filled=float(d.get("size_matched") or 0),
                        status=OrderStatus.OPEN,
                        raw=d,
                    )
                )
        except Exception as exc:
            log.error("polymarket get_open_orders failed: %s", exc)
        return orders

    def get_positions(self) -> list[Position]:
        # Positions come from the Data API / SDK account endpoints; the bot
        # also tracks its own fills locally (see bot/executor.py), which is
        # the source of truth for risk limits.
        return []

    def get_balance(self) -> float:
        try:
            sdk = self._sdk_client()
            bal = getattr(sdk, "get_balance", None)
            if bal is not None:
                v = bal()
                return float(v.get("balance", 0) if isinstance(v, dict) else v)
        except Exception as exc:
            log.error("polymarket get_balance failed: %s", exc)
        return 0.0
