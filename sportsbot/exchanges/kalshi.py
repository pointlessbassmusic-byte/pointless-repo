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

from sportsbot.core.books import sell_levels, walk_sell
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


FEE_RATE = 0.07
# Series whose fee schedule carries a multiplier (verified 2026-09; the
# match is on the SERIES prefix, never a loose substring — a bare "MLB" in
# a ticker would mis-price unrelated markets).
FEE_MULTIPLIERS = {"KXMLBGAME": 0.5}


def kalshi_fee_multiplier(market_id: str) -> float:
    """Fee multiplier for a ticker, by series prefix. Unknown series pay
    full rate — never assume a discount that may not exist."""
    for series, mult in FEE_MULTIPLIERS.items():
        if (market_id or "").startswith(series):
            return mult
    return 1.0


def kalshi_taker_fee(price: float, contracts: float, fee_multiplier: float = 1.0) -> float:
    """TOTAL settled fee for one order: ceil-to-cent(0.07 × mult × C × P × (1−P)).

    The ceil applies ONCE PER ORDER, so this is NOT linear in `contracts`:
    calling it with contracts=1.0 does not yield the marginal per-share fee
    (at P=0.20 it returns $0.02 against a true marginal of $0.0112). Use it
    for what the venue actually charges — execution and accounting — and use
    `kalshi_fee_per_share` for edge/sizing/exit math.
    """
    raw = FEE_RATE * fee_multiplier * contracts * price * (1.0 - price)
    # round() guards against FP noise (e.g. 1.7500000000000002) inflating the ceil
    return math.ceil(round(raw * 100.0, 6)) / 100.0


def kalshi_fee_per_share(price: float, fee_multiplier: float = 1.0) -> float:
    """MARGINAL fee per contract — linear, no rounding.

    This is the number every decision path needs (edge thresholds, Kelly
    sizing, exit value, arb cost). Quantising it to whole cents by way of
    `kalshi_taker_fee(price, 1.0)` inflates modelled cost by up to ~2x
    inside the bot's [0.15, 0.85] entry band, which silently suppresses
    real trades and can trip the exit hard stop on phantom cost.
    """
    return FEE_RATE * fee_multiplier * price * (1.0 - price)


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
        self._pair_event_siblings(out)
        return out

    @staticmethod
    def _pair_event_siblings(markets: list[MarketInfo]) -> None:
        """Kalshi's no_sub_title mirrors the YES-side player instead of
        naming the opponent, so a market parses with home == away — which
        would make the scanner rate a player against themselves. Each match
        event carries one market per player, so the true opponent is the
        SIBLING market's home within the same event_ticker."""
        by_event: dict[str, list[MarketInfo]] = {}
        for mi in markets:
            ev = (mi.meta or {}).get("event_ticker")
            if ev:
                by_event.setdefault(ev, []).append(mi)
        for group in by_event.values():
            if len(group) != 2:
                continue
            a, b = group
            if not a.home or not b.home or a.home == b.home:
                continue
            if not a.away or a.away == a.home:
                a.away = b.home
            if not b.away or b.away == b.home:
                b.away = a.home

    @staticmethod
    def _dollars(m: dict, field: str) -> Optional[float]:
        v = m.get(field)
        if v in (None, ""):
            return None
        return float(Decimal(str(v)))

    @staticmethod
    def _ts(m: dict, *fields: str):
        from datetime import datetime, timezone

        for f in fields:
            v = m.get(f)
            if not v:
                continue
            try:
                dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    def _to_market_info(self, m: dict, sport: Sport, series: str) -> MarketInfo:
        # occurrence_datetime is the scheduled match START (verified live:
        # 2026-09-22T07:00Z for a 26SEP22 ticker). close_time and
        # expiration_time are FAR-FUTURE legal bounds (+2 weeks — the market
        # trades through the match), so they must never be the start proxy:
        # the pre-match cutoff would never trigger and the bot would enter
        # in-play. expected_expiration_time tracks the real settle window.
        start_time = self._ts(m, "occurrence_datetime")
        close_time = self._ts(m, "expected_expiration_time", "close_time")
        return MarketInfo(
            exchange=Exchange.KALSHI,
            market_id=m.get("ticker", ""),
            question=m.get("title", ""),
            slug=m.get("ticker", ""),
            sport=sport,
            # yes_sub_title names the player the YES contract pays on;
            # no_sub_title MIRRORS it (verified live), so the true opponent
            # is recovered by _pair_event_siblings after discovery.
            home=m.get("yes_sub_title") or m.get("subtitle") or None,
            away=m.get("no_sub_title") or None,
            start_time=start_time,
            close_time=close_time,
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

    def get_resolution(self, market_id: str) -> Optional[bool]:
        """True/False once the market settles with a yes/no result."""
        try:
            data = self._request("GET", f"{API_ROOT}/markets/{market_id}")
        except httpx.HTTPError:
            return None
        m = data.get("market", data)
        if m.get("status") not in ("settled", "finalized", "determined"):
            return None
        result = (m.get("result") or "").lower()
        if result == "yes":
            return True
        if result == "no":
            return False
        return None

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

    def close_position(self, market_id: str, side: Side, quote: MarketQuote,
                       min_price: float = 0.02) -> Optional[dict]:
        """Close an open position by buying the opposite side — Kalshi nets
        YES against NO automatically, so buying NO while holding YES settles
        the pair to cash. The IOC limit is priced so net proceeds per
        contract are never below `min_price`; an empty or collapsed book
        closes nothing (the caller holds and retries).

        Returns {closed_size, avg_price, proceeds, fee} like the paper
        venue, with avg_price/proceeds derived from walking the quote's
        levels for the filled count (the same levels the IOC crossed)."""
        pos_size = 0.0
        for p in self.get_positions():
            if p.market_id == market_id and p.side == side and p.size > 0:
                pos_size = p.size
                break
        if pos_size <= 0:
            return None
        exit_avg, sellable = walk_sell(sell_levels(quote, side),
                                       min_price, pos_size)
        if sellable <= 0 or exit_avg <= 0:
            return None

        opposite = Side.NO if side == Side.YES else Side.YES
        order = Order(
            client_id=f"close-{int(time.time() * 1000)}",
            market_id=market_id,
            side=opposite,
            # price of the side being bought; proceeds/contract >= min_price
            price=round(1.0 - min_price, 4),
            size=sellable,
            order_type=OrderType.IOC,
        )
        placed = self.place_order(order)
        if placed.status == OrderStatus.REJECTED or placed.filled <= 0:
            return None
        avg, _ = walk_sell(sell_levels(quote, side), min_price, placed.filled)
        fee = kalshi_taker_fee(avg, placed.filled,
                               fee_multiplier=kalshi_fee_multiplier(market_id))
        return {"closed_size": placed.filled, "avg_price": round(avg, 4),
                "proceeds": round(avg * placed.filled - fee, 4),
                "fee": round(fee, 4)}

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
