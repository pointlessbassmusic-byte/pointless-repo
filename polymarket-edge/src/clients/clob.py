"""Polymarket CLOB client — prices for free, orders via py-clob-client when live.

Read endpoints (no auth): https://clob.polymarket.com
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..http_util import retrying_session

log = logging.getLogger(__name__)

CLOB_BASE = "https://clob.polymarket.com"


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
        # /prices is a read-only POST, so POST retries are safe here
        self.http = retrying_session(allow_post=True)
        self._private_key = private_key
        self._funder = funder
        self._trader = None  # authed py_clob_client.client.ClobClient

    # ---- reads (no auth) ----

    def quotes(self, token_ids: list[str], chunk: int = 100) -> dict[str, Quote]:
        """Best bid/ask for a batch of token ids via the /prices endpoint.

        Chunked: with hundreds of tokens (weather markets), a single giant
        POST can stall or be rejected; 100 tokens = 200 body entries per call.
        """
        out: dict[str, Quote] = {}
        for i in range(0, len(token_ids), chunk):
            ids = token_ids[i:i + chunk]
            body = [{"token_id": t, "side": s} for t in ids for s in ("BUY", "SELL")]
            r = self.http.post(f"{CLOB_BASE}/prices", json=body, timeout=30)
            r.raise_for_status()
            data = r.json()  # {token_id: {"BUY": "...", "SELL": "..."}}
            for t in ids:
                entry = data.get(t, {})
                # side=BUY returns the best bid (highest resting buy order);
                # side=SELL returns the best ask (lowest resting sell order)
                bid = float(entry["BUY"]) if entry.get("BUY") else None
                ask = float(entry["SELL"]) if entry.get("SELL") else None
                out[t] = Quote(token_id=t, bid=bid, ask=ask)
            if len(token_ids) > chunk:
                log.info("clob quotes: %d/%d tokens", min(i + chunk, len(token_ids)),
                         len(token_ids))
        return out

    # ---- writes (auth; only used with --live) ----
    # Live orders go through the official `polymarket-client` py-sdk — the old
    # py-clob-client is archived and targets retired V1 contracts; never
    # reintroduce it. Imported lazily so dry-run needs no SDK installed.

    def _get_trader(self):
        if self._trader is None:
            if not self._private_key:
                raise RuntimeError("POLYMARKET_PRIVATE_KEY not set — cannot trade live")
            secure_client_cls = _import_secure_client()
            kwargs: dict = {"private_key": self._private_key}
            if self._funder:
                kwargs["wallet"] = self._funder
            self._trader = secure_client_cls.create(**kwargs)
        return self._trader

    def place_limit_buy(self, token_id: str, price: float, size_shares: float) -> dict:
        trader = self._get_trader()
        resp = trader.place_limit_order(
            token_id=token_id, side="BUY",
            price=round(price, 3), size=round(size_shares, 2),
        )
        data = resp if isinstance(resp, dict) else getattr(resp, "__dict__", {"resp": str(resp)})
        order_id = str(data.get("order_id") or data.get("orderID") or data.get("id") or "")
        error = str(data.get("error") or data.get("errorMsg") or "")
        # keep the shape the executor expects ({success, errorMsg, orderID})
        out = {"success": bool(order_id) and not error, "orderID": order_id,
               "errorMsg": error, "raw": data}
        log.info("placed order: %s", out)
        return out


def _import_secure_client():
    try:
        from polymarket import SecureClient
    except ImportError:
        try:
            from polymarket_client import SecureClient
        except ImportError as exc:
            raise RuntimeError(
                "polymarket-client SDK not installed — pip install polymarket-client"
            ) from exc
    return SecureClient
