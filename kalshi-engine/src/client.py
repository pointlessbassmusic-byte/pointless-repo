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

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

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

    @property
    def mid(self) -> float:
        if self.yes_bid > 0 and self.yes_ask > 0:
            return (self.yes_bid + self.yes_ask) / 2
        return self.last_price


def _cents_to_prob(c) -> float:
    return (c or 0) / 100.0


def _parse_market(m: dict) -> Market:
    exp = None
    for key in ("expiration_time", "close_time"):
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
        yes_bid=_cents_to_prob(m.get("yes_bid")),
        yes_ask=_cents_to_prob(m.get("yes_ask")),
        last_price=_cents_to_prob(m.get("last_price")),
        volume=int(m.get("volume") or 0),
        open_interest=int(m.get("open_interest") or 0),
        expiration=exp,
        status=m.get("status", ""),
    )


class KalshiClient:
    def __init__(self, api_key_id: str = "", private_key_path: str = "", demo: bool = True):
        self.base = DEMO_BASE if demo else PROD_BASE
        self.api_key_id = api_key_id
        self.http = requests.Session()
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
        if auth:
            headers.update(self._auth_headers(method, full_path))
        r = self.http.request(method, f"{self.base}{full_path}", headers=headers, timeout=30, **kwargs)
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
