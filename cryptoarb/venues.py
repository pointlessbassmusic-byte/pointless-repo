"""Public market-data clients. Read-only, no API keys, no orders.

Binance is deliberately absent: it answers 451 from US IPs (verified), and
this project never routes around a venue's geoblock.
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

CA_BUNDLE = "/root/.ccr/ca-bundle.crt"
UA = {"User-Agent": "cryptoarb/1.0"}


def _ctx():
    try:
        return ssl.create_default_context(cafile=CA_BUNDLE)
    except (FileNotFoundError, ssl.SSLError):
        return ssl.create_default_context()


def http_json(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as resp:
        return json.load(resp)


@dataclass
class Top:
    """Top of book for one instrument on one venue."""

    venue: str
    symbol: str
    bid: float
    ask: float
    bid_size: float = 0.0
    ask_size: float = 0.0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.bid * 10_000 if self.bid else 0.0

    @property
    def ok(self) -> bool:
        """A crossed or one-sided book is unusable, never tradeable."""
        return self.bid > 0 and self.ask > 0 and self.ask >= self.bid


def coinbase_top(symbol: str) -> Optional[Top]:
    try:
        d = http_json(f"https://api.exchange.coinbase.com/products/{symbol}/ticker")
        return Top("coinbase", symbol, float(d["bid"]), float(d["ask"]),
                   float(d.get("size") or 0.0), float(d.get("size") or 0.0))
    except Exception as exc:
        log.warning("coinbase %s: %s", symbol, exc)
        return None


def kraken_top(pair: str) -> Optional[Top]:
    try:
        d = http_json(f"https://api.kraken.com/0/public/Ticker?pair={pair}")
        res = d.get("result") or {}
        if not res:
            return None
        k = next(iter(res))
        b, a = res[k]["b"], res[k]["a"]
        return Top("kraken", pair, float(b[0]), float(a[0]),
                   float(b[2]), float(a[2]))
    except Exception as exc:
        log.warning("kraken %s: %s", pair, exc)
        return None


def kucoin_top(symbol: str) -> Optional[Top]:
    try:
        d = http_json(
            f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={symbol}")
        t = d.get("data") or {}
        if not t.get("bestBid"):
            return None
        return Top("kucoin", symbol, float(t["bestBid"]), float(t["bestAsk"]),
                   float(t.get("bestBidSize") or 0.0),
                   float(t.get("bestAskSize") or 0.0))
    except Exception as exc:
        log.warning("kucoin %s: %s", symbol, exc)
        return None


def kucoin_all_tickers() -> dict:
    """Whole-venue snapshot in ONE request — triangular scanning without
    burning rate limit on dozens of calls."""
    try:
        d = http_json("https://api.kucoin.com/api/v1/market/allTickers")
        return {t["symbol"]: t for t in (d.get("data") or {}).get("ticker", [])
                if t.get("buy") and t.get("sell")}
    except Exception as exc:
        log.warning("kucoin allTickers: %s", exc)
        return {}


# Same asset, per-venue symbol. Kraken quotes USD, KuCoin quotes USDT —
# they are not identical instruments; see strategies.CROSS_QUOTE_RISK_BPS.
SPOT_UNIVERSE = {
    "BTC": {"coinbase": "BTC-USD", "kraken": "XBTUSD", "kucoin": "BTC-USDT"},
    "ETH": {"coinbase": "ETH-USD", "kraken": "ETHUSD", "kucoin": "ETH-USDT"},
    "SOL": {"coinbase": "SOL-USD", "kraken": "SOLUSD", "kucoin": "SOL-USDT"},
    "XRP": {"coinbase": "XRP-USD", "kraken": "XRPUSD", "kucoin": "XRP-USDT"},
    "LINK": {"coinbase": "LINK-USD", "kraken": "LINKUSD", "kucoin": "LINK-USDT"},
}

_FETCH = {"coinbase": coinbase_top, "kraken": kraken_top, "kucoin": kucoin_top}


def spot_snapshot(assets=None) -> dict:
    """{asset: {venue: Top}} across the configured universe."""
    out: dict = {}
    for asset, venues in SPOT_UNIVERSE.items():
        if assets and asset not in assets:
            continue
        tops = {}
        for venue, sym in venues.items():
            t = _FETCH[venue](sym)
            if t is not None and t.ok:
                tops[venue] = t
        if len(tops) >= 2:
            out[asset] = tops
    return out


def polymarket_binary_books(limit_events: int = 40, max_markets: int = 60) -> list:
    """Two-outcome Polymarket markets with both order books.

    Returns [{market_id, question, asks: [yes_ask, no_ask], sizes: [...]}].
    """
    out: list = []
    try:
        evs = http_json("https://gamma-api.polymarket.com/events"
                        f"?closed=false&limit={limit_events}"
                        "&order=volume24hr&ascending=false")
    except Exception as exc:
        log.warning("gamma events: %s", exc)
        return out
    for ev in evs if isinstance(evs, list) else []:
        for m in (ev.get("markets") or []):
            if len(out) >= max_markets:
                return out
            if not m.get("enableOrderBook") or m.get("closed"):
                continue
            try:
                toks = json.loads(m.get("clobTokenIds") or "[]")
                if len(toks) != 2:
                    continue
                asks, sizes = [], []
                for t in toks:
                    bk = http_json(f"https://clob.polymarket.com/book?token_id={t}")
                    lv = bk.get("asks") or []
                    if not lv:
                        raise ValueError("one-sided book")
                    best = min(lv, key=lambda x: float(x["price"]))
                    asks.append(float(best["price"]))
                    sizes.append(float(best["size"]))
                out.append({"market_id": m.get("conditionId") or m.get("id"),
                            "question": (m.get("question") or "")[:90],
                            "asks": asks, "sizes": sizes})
            except Exception:
                continue
    return out
