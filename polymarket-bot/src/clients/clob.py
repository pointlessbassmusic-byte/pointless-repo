"""Polymarket CLOB client — prices for free, orders via py-clob-client when live.

Read endpoints (no auth): https://clob.polymarket.com
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

CLOB_BASE = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137


@dataclass
class Quote:
    token_id: str
    bid: float | None
    ask: float | None

    @property
    def mid(self) -> float | None:
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2
        return self.bid if self.bid is not None else self.ask


class ClobClient:
    """Thin read-only wrapper; lazily builds an authed py-clob-client for live orders."""

    def __init__(self, private_key: str = "", funder: str = ""):
        self.http = requests.Session()
        self._private_key = private_key
        self._funder = funder
        self._trader = None  # authed py_clob_client.client.ClobClient

    # ---- reads (no auth) ----

    def quotes(self, token_ids: list[str]) -> dict[str, Quote]:
        """Best bid/ask for a batch of token ids via the /prices endpoint."""
        out: dict[str, Quote] = {}
        if not token_ids:
            return out
        body = [{"token_id": t, "side": s} for t in token_ids for s in ("BUY", "SELL")]
        r = self.http.post(f"{CLOB_BASE}/prices", json=body, timeout=30)
        r.raise_for_status()
        data = r.json()  # {token_id: {"BUY": "...", "SELL": "..."}}
        for t in token_ids:
            entry = data.get(t, {})
            # side=BUY returns the best bid (highest resting buy order);
            # side=SELL returns the best ask (lowest resting sell order)
            bid = float(entry["BUY"]) if entry.get("BUY") else None
            ask = float(entry["SELL"]) if entry.get("SELL") else None
            out[t] = Quote(token_id=t, bid=bid, ask=ask)
        return out

    # ---- writes (auth; only used with --live) ----

    def _get_trader(self):
        if self._trader is None:
            from py_clob_client.client import ClobClient as PyClobClient

            if not self._private_key:
                raise RuntimeError("POLYMARKET_PRIVATE_KEY not set — cannot trade live")
            kwargs = dict(key=self._private_key, chain_id=POLYGON_CHAIN_ID)
            if self._funder:
                # signature_type 1 = email/magic proxy, 2 = browser wallet proxy
                kwargs.update(signature_type=2, funder=self._funder)
            client = PyClobClient(CLOB_BASE, **kwargs)
            client.set_api_creds(client.create_or_derive_api_creds())
            self._trader = client
        return self._trader

    def place_limit_buy(self, token_id: str, price: float, size_shares: float) -> dict:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        trader = self._get_trader()
        order = trader.create_order(
            OrderArgs(price=round(price, 3), size=round(size_shares, 2), side=BUY, token_id=token_id)
        )
        resp = trader.post_order(order, OrderType.GTC)
        log.info("placed order: %s", resp)
        return resp
