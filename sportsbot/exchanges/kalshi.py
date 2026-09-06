"""Kalshi Trade API v2 client (2026 surface: fixed-point dollar strings,
Create Order V2). Thin httpx implementation — the official SDKs regenerate
weekly; ~100 lines of signing beats chasing that churn.

Auth: RSA-PSS-SHA256 over f"{timestamp_ms}{METHOD}{path}" (path from API
root, NO query string), base64 in KALSHI-ACCESS-SIGNATURE, with
KALSHI-ACCESS-KEY and KALSHI-ACCESS-TIMESTAMP (unix ms). Public market-data
endpoints need no auth, so scanning works without credentials.

Breaking changes this client is built for (verified 2026-09):
* integer-cent price fields are gone — parse ``yes_bid_dollars`` etc.
  (fixed-point strings) with Decimal, never float.
* ``POST /portfolio/orders`` is 410 Gone — orders go to
  ``POST /portfolio/events/orders`` with side "bid"(=long YES) / "ask"
  (=long NO) and a YES-terms dollar-string price.

Sports series (verified): tennis KXATPMATCH / KXWTAMATCH (+challenger,
doubles, slams), MLB KXMLBGAME (fee_multiplier 0.5), table tennis
KXTABLETENNISMATCH / KXWTABLETENNISMATCH.
"""

from __future__ import annotations

import base64
import logging
import math
import os
import time
from decimal import Decimal
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

PROD_BASE = "https://api.elections.kalshi.com"
DEMO_BASE = "https://demo-api.kalshi.co"
API_ROOT = "/trade-api/v2"

SPORT_SERIES: dict[str, list[str]] = {
    "tennis": ["KXATPMATCH", "KXWTAMATCH", "KXATPCHALLENGERMATCH", "KXWTACHALLENGERMATCH"],
    "baseball": ["KXMLBGAME"],
    "table_tennis": ["KXTABLETENNISMATCH", "KXWTABLETENNISMATCH"],
}

SPORT_FOR_KEY = {
    "tennis": Sport.TENNIS,
    "baseball": Sport.BASEBALL,
    "table_tennis": Sport.TABLE_TENNIS,
}


def kalshi_taker_fee(price: float, contracts: float, fee_multiplier: float = 1.0) -> float:
    """ceil-to-cent(0.07 × mult × C × P × (1−P)); P in dollars.

    KXMLBGAME runs fee_multiplier 0.5. Maker fee is 25% of taker on
    quadratic_with_maker_fees series — prefer resting orders.
    """
    raw = 0.07 * fee_multiplier * contracts * price * (1.0 - price)
    # round() guards against FP noise (e.g. 1.7500000000000002) inflating the ceil
    return math.ceil(round(raw * 100.0, 6)) / 100.0


class KalshiClient(ExchangeClient):
    exchange = Exchange.KALSHI

    def __init__(
        self,
        api_key_id: Optional[str] = None,
        private_key_path: Optional[str] = None,
        env: Optional[str] = None,
        timeout: float = 15.0,
    ) -> None:
        self.api_key_id = api_key_id or os.environ.get("KALSHI_API_KEY_ID") or None
        self.private_key_path = private_key_path or os.environ.get("KALSHI_PRIVATE_KEY_PATH") or None
        env = (env or os.environ.get("KALSHI_ENV") or "demo").lower()
        self.base = PROD_BASE if env == "prod" else DEMO_BASE
        self.http = httpx.Client(timeout=timeout, headers={"User-Agent": "sportsbot/1.0"})
        self._private_key = None

    # ------------------------------------------------------------------
    # Signing
    # ------------------------------------------------------------------
    def _load_key(self):
        if self._private_key is None:
            if not self.private_key_path:
                raise RuntimeError(
                    "KALSHI_PRIVATE_KEY_PATH not set — authed endpoints unavailable "
                    "(public market data still works)"
                )
            from cryptography.hazmat.primitives import serialization

            with open(self.private_key_path, "rb") as fh:
                self._private_key = serialization.load_pem_private_key(fh.read(), password=None)
        return self._private_key

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        if not self.api_key_id:
            raise RuntimeError("KALSHI_API_KEY_ID not set")
        ts = str(int(time.time() * 1000))
        msg = f"{ts}{method.upper()}{path.split('?')[0]}".encode()
        sig = self._load_key().sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=8), reraise=True)
    def _request(self, method: str, path: str, params: dict | None = None,
                 json_body: dict | None = None, auth: bool = False) -> Any:
        url = f"{self.base}{path}"
        headers = self._auth_headers(method, path) if auth else {}
        resp = self.http.request(method, url, params=params, json=json_body, headers=headers)
        if resp.status_code == 429:
            # Kalshi sends no Retry-After; let tenacity back off.
            raise httpx.HTTPStatusError("rate limited", request=resp.request, response=resp)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # ------------------------------------------------------------------
    # Discovery / quotes (public)
    # ------------------------------------------------------------------
    def list_sports_markets(self, sport_tag: str) -> list[MarketInfo]:
        series_list = SPORT_SERIES.get(sport_tag)
        if not series_list:
            raise ValueError(f"unknown sport {sport_tag!r}; known: {sorted(SPORT_SERIES)}")
        sport = SPORT_FOR_KEY[sport_tag]
        out: list[MarketInfo] = []
        for series in series_list:
            cursor = None
            while True:
                params: dict[str, Any] = {
                    "series_ticker": series,
                    "status": "open",
                    "mve_filter": "exclude",
                    "limit": 200,
                }
                if cursor:
                    params["cursor"] = cursor
                data = self._request("GET", f"{API_ROOT}/markets", params=params)
                for m in data.get("markets", []):
                    out.append(self._to_market_info(m, sport, series))
                cursor = data.get("cursor")
                if not cursor or not data.get("markets"):
                    break
        return out

    @staticmethod
    def _dollars(m: dict, field: str) -> Optional[float]:
        v = m.get(field)
        if v in (None, ""):
            return None
        return float(Decimal(str(v)))

    def _to_market_info(self, m: dict, sport: Sport, series: str) -> MarketInfo:
        return MarketInfo(
            exchange=Exchange.KALSHI,
            market_id=m.get("ticker", ""),
            question=m.get("title", ""),
            slug=m.get("ticker", ""),
            sport=sport,
            # yes_sub_title names the outcome the YES contract pays on.
            home=m.get("yes_sub_title") or m.get("subtitle") or None,
            away=m.get("no_sub_title") or None,
            start_time=None,
            close_time=None,
            active=m.get("status") in ("active", "open"),
            tick_size=0.01,
            min_order_size=1.0,
            meta={
                "series": series,
                "event_ticker": m.get("event_ticker"),
                "yes_bid": self._dollars(m, "yes_bid_dollars"),
                "yes_ask": self._dollars(m, "yes_ask_dollars"),
                "last_price": self._dollars(m, "last_price_dollars"),
                "volume_fp": m.get("volume_fp"),
                "open_interest_fp": m.get("open_interest_fp"),
            },
        )

    def get_quote(self, market: MarketInfo) -> MarketQuote:
        data = self._request(
            "GET", f"{API_ROOT}/markets/{market.market_id}/orderbook", params={"depth": 10}
        )
        ob = data.get("orderbook_fp") or data.get("orderbook") or {}
        yes_bids_raw = ob.get("yes_dollars") or []
        no_bids_raw = ob.get("no_dollars") or []
        # Both sides are BIDS. A NO bid at price q is a YES ask at 1 - q.
        bids = sorted(
            (BookLevel(price=float(Decimal(p)), size=float(Decimal(s))) for p, s in yes_bids_raw),
            key=lambda lvl: -lvl.price,
        )
        asks = sorted(
            (BookLevel(price=round(1.0 - float(Decimal(p)), 4), size=float(Decimal(s)))
             for p, s in no_bids_raw),
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
    # Trading (Create Order V2)
    # ------------------------------------------------------------------
    def place_order(self, order: Order) -> Order:
        # Engine semantics: we BUY `order.side` at `order.price` (price of that
        # side). Kalshi V2 books everything in YES terms:
        #   buy YES @ p  -> side "bid", price = p
        #   buy NO  @ p  -> side "ask", price = 1 - p (YES-terms price)
        if order.side == Side.YES:
            k_side, k_price = "bid", order.price
        else:
            k_side, k_price = "ask", round(1.0 - order.price, 4)
        tif = {
            OrderType.LIMIT: "good_till_canceled",
            OrderType.LIMIT_GTD: "good_till_canceled",
            OrderType.FOK: "fill_or_kill",
            OrderType.IOC: "immediate_or_cancel",
        }[order.order_type]
        body: dict[str, Any] = {
            "ticker": order.market_id,
            "side": k_side,
            "count": f"{order.size:.2f}",
            "price": f"{k_price:.4f}",
            "time_in_force": tif,
            "client_order_id": order.client_id,
            "self_trade_prevention_type": "maker",
        }
        try:
            resp = self._request(
                "POST", f"{API_ROOT}/portfolio/events/orders", json_body=body, auth=True
            )
            payload = resp.get("order", resp)
            order.order_id = str(payload.get("order_id", ""))
            fill = payload.get("fill_count")
            order.filled = float(Decimal(str(fill))) if fill not in (None, "") else 0.0
            order.status = (
                OrderStatus.FILLED if order.filled >= order.size else
                OrderStatus.PARTIAL if order.filled > 0 else OrderStatus.OPEN
            )
            order.raw = {"response": resp}
        except Exception as exc:
            log.error("kalshi order failed: %s", exc)
            order.status = OrderStatus.REJECTED
            order.raw = {"error": str(exc)}
        return order

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._request("DELETE", f"{API_ROOT}/portfolio/orders/{order_id}", auth=True)
            return True
        except Exception as exc:
            log.error("kalshi cancel failed: %s", exc)
            return False

    def get_open_orders(self) -> list[Order]:
        try:
            data = self._request(
                "GET", f"{API_ROOT}/portfolio/orders", params={"status": "resting"}, auth=True
            )
        except Exception as exc:
            log.error("kalshi get_open_orders failed: %s", exc)
            return []
        orders = []
        for o in data.get("orders", []):
            outcome = o.get("outcome_side") or ("yes" if o.get("side") == "bid" else "no")
            orders.append(
                Order(
                    order_id=str(o.get("order_id", "")),
                    client_id=str(o.get("client_order_id") or ""),
                    exchange=Exchange.KALSHI,
                    market_id=o.get("ticker", ""),
                    side=Side.YES if outcome == "yes" else Side.NO,
                    price=0.0,
                    size=0.0,
                    status=OrderStatus.OPEN,
                    raw=o,
                )
            )
        return orders

    def get_positions(self) -> list[Position]:
        try:
            data = self._request("GET", f"{API_ROOT}/portfolio/positions", auth=True)
        except Exception as exc:
            log.error("kalshi get_positions failed: %s", exc)
            return []
        positions = []
        for p in data.get("market_positions", []):
            qty = p.get("position_fp") or p.get("position") or 0
            qty = float(Decimal(str(qty)))
            if qty == 0:
                continue
            positions.append(
                Position(
                    market_id=p.get("ticker", ""),
                    side=Side.YES if qty > 0 else Side.NO,
                    size=abs(qty),
                )
            )
        return positions

    def get_balance(self) -> float:
        try:
            data = self._request("GET", f"{API_ROOT}/portfolio/balance", auth=True)
            for field, scale in (("balance_fp", 1.0), ("balance", 0.01)):
                if field in data and data[field] not in (None, ""):
                    return float(Decimal(str(data[field]))) * scale
        except Exception as exc:
            log.error("kalshi get_balance failed: %s", exc)
        return 0.0
