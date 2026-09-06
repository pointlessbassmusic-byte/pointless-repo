"""Kalshi Trade API v2 client with RSA-PSS request signing.

Auth scheme (current as of 2025/2026):
  KALSHI-ACCESS-KEY:       API key id
  KALSHI-ACCESS-TIMESTAMP: unix milliseconds
  KALSHI-ACCESS-SIGNATURE: base64( RSA-PSS-SHA256( timestamp + METHOD + path ) )
Path excludes query string. Public market reads work unauthenticated.
"""
from __future__ import annotations

import base64
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .http_util import retrying_session

log = logging.getLogger(__name__)

PROD_BASE = "https://api.elections.kalshi.com"
DEMO_BASE = "https://demo-api.kalshi.co"
API_PREFIX = "/trade-api/v2"


@dataclass
class Market:
    ticker: str
    event_ticker: str
    title: str
    yes_bid: float   # dollars, 0-1
    yes_ask: float
    last_price: float
    volume: int
    open_interest: int
    expiration: datetime | None
    status: str
    result: str = ""     # "yes" | "no" once settled, else ""

    @property
    def mid(self) -> float:
        if self.yes_bid > 0 and self.yes_ask > 0:
            return (self.yes_bid + self.yes_ask) / 2
        return self.last_price


def _price(m: dict, dollars_key: str, cents_key: str) -> float:
    """Price 0-1. Current API serves string dollars ("0.6300"); older payloads
    served integer cents under the legacy key."""
    v = m.get(dollars_key)
    if v is not None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    return (m.get(cents_key) or 0) / 100.0


def _count(m: dict, fp_key: str, int_key: str) -> int:
    """Contract count. Current API serves fixed-point strings ("1663.42")."""
    v = m.get(fp_key)
    if v is not None:
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0
    return int(m.get(int_key) or 0)


def _parse_market(m: dict) -> Market:
    exp = None
    # close_time is when trading actually stops; expiration_time is often a
    # far-future legal bound (e.g. "one week after the event", or years out)
    for key in ("close_time", "expected_expiration_time", "expiration_time"):
        if m.get(key):
            try:
                exp = datetime.fromisoformat(m[key].replace("Z", "+00:00")).astimezone(timezone.utc)
                break
            except ValueError:
                pass
    return Market(
        ticker=m.get("ticker", ""),
        event_ticker=m.get("event_ticker", ""),
        title=m.get("title", ""),
        yes_bid=_price(m, "yes_bid_dollars", "yes_bid"),
        yes_ask=_price(m, "yes_ask_dollars", "yes_ask"),
        last_price=_price(m, "last_price_dollars", "last_price"),
        volume=_count(m, "volume_fp", "volume"),
        open_interest=_count(m, "open_interest_fp", "open_interest"),
        expiration=exp,
        status=m.get("status", ""),
        result=m.get("result", "") or "",
    )


class KalshiClient:
    def __init__(self, api_key_id: str = "", private_key_path: str = "", demo: bool = True,
                 read_prod: bool = True):
        # Orders/portfolio go to the trading env (demo until proven). Public market
        # reads default to prod either way: demo market data is synthetic (zero
        # volume/price), so dry-running against it proves nothing. Set read_prod
        # false only to test the demo order flow end-to-end on demo tickers.
        self.trade_base = DEMO_BASE if demo else PROD_BASE
        self.read_base = PROD_BASE if read_prod else self.trade_base
        self.api_key_id = api_key_id
        # GET-only retries: order placement (POST) must never auto-retry
        self.http = retrying_session()
        self._private_key = None
        if private_key_path:
            try:
                with open(private_key_path, "rb") as f:
                    self._private_key = serialization.load_pem_private_key(f.read(), password=None)
            except FileNotFoundError:
                log.warning("Kalshi private key not found at %s — auth requests will fail", private_key_path)

    # ---- signing ----

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        if not (self.api_key_id and self._private_key):
            raise RuntimeError("Kalshi API credentials not configured")
        ts = str(int(time.time() * 1000))
        msg = f"{ts}{method}{path}".encode()
        sig = self._private_key.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        }

    def _request(self, method: str, path: str, auth: bool = False, **kwargs) -> dict:
        full_path = f"{API_PREFIX}{path}"
        headers = kwargs.pop("headers", {})
        base = self.trade_base if auth else self.read_base
        if auth:
            headers.update(self._auth_headers(method, full_path))
        r = self.http.request(method, f"{base}{full_path}", headers=headers, timeout=30, **kwargs)
        r.raise_for_status()
        return r.json()

    # ---- public reads ----

    def markets(self, statuses: list[str] | None = None, series_ticker: str | None = None,
                max_markets: int = 500) -> list[Market]:
        out: list[Market] = []
        cursor = None
        while len(out) < max_markets:
            params: dict = {"limit": min(200, max_markets - len(out))}
            if statuses:
                params["status"] = ",".join(statuses)
            if series_ticker:
                params["series_ticker"] = series_ticker
            if cursor:
                params["cursor"] = cursor
            data = self._request("GET", "/markets", params=params)
            batch = data.get("markets", [])
            out.extend(_parse_market(m) for m in batch)
            cursor = data.get("cursor")
            if not cursor or not batch:
                break
        log.info("kalshi: fetched %d markets", len(out))
        return out

    def markets_via_events(self, max_events: int = 500,
                           categories: list[str] | None = None) -> list[Market]:
        """Open markets discovered through /events — the curated feed.

        The raw /markets firehose is dominated by auto-generated multivariate
        shard markets; /events returns real events (with category), and nesting
        pulls each event's markets in the same call.
        """
        out: list[Market] = []
        seen_events = 0
        cursor = None
        while seen_events < max_events:
            params: dict = {"limit": min(200, max_events - seen_events),
                            "status": "open", "with_nested_markets": "true"}
            if cursor:
                params["cursor"] = cursor
            data = self._request("GET", "/events", params=params)
            events = data.get("events", [])
            seen_events += len(events)
            for ev in events:
                if categories and ev.get("category") not in categories:
                    continue
                out.extend(_parse_market(m) for m in ev.get("markets") or [])
            cursor = data.get("cursor")
            if not cursor or not events:
                break
        log.info("kalshi: %d markets from %d events", len(out), seen_events)
        return out

    def markets_by_tickers(self, tickers: list[str]) -> list[Market]:
        """Fetch specific markets (any status) — used to look up settlements."""
        out: list[Market] = []
        for i in range(0, len(tickers), 20):
            chunk = tickers[i:i + 20]
            data = self._request("GET", "/markets", params={"tickers": ",".join(chunk)})
            out.extend(_parse_market(m) for m in data.get("markets", []))
        return out

    # ---- authed ----

    def balance(self) -> float:
        data = self._request("GET", "/portfolio/balance", auth=True)
        return data.get("balance", 0) / 100.0

    def place_limit_order(self, ticker: str, side: str, price_prob: float, count: int) -> dict:
        """Buy `count` contracts of yes/no at a limit price (probability 0-1 → cents)."""
        assert side in ("yes", "no")
        cents = max(1, min(99, round(price_prob * 100)))
        body = {
            "ticker": ticker,
            "client_order_id": str(uuid.uuid4()),
            "action": "buy",
            "side": side,
            "type": "limit",
            "count": count,
            f"{side}_price": cents,
        }
        resp = self._request("POST", "/portfolio/orders", auth=True, json=body)
        log.info("placed kalshi order: %s", resp.get("order", {}).get("order_id"))
        return resp
