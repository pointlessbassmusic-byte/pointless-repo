"""Polymarket US (QCEX DCM) client — the US-legal Polymarket path.

Two hosts: market data is PUBLIC on `gateway.polymarket.us` (no key needed,
verified live 2026-10-09), trading is on `api.polymarket.us` and every
request is Ed25519-signed: headers `X-PM-Access-Key` (key id, a UUID),
`X-PM-Timestamp` (ms since epoch, ±30 s of server time) and
`X-PM-Signature` (base64 Ed25519 over ``f"{timestamp}{METHOD}{path}"``; the
body is not signed). The secret is base64; its first 32 decoded bytes are
the private key (docs.polymarket.us/api-reference/authentication).

Prices are in dollars per contract and the whole book is quoted in LONG
(YES) terms: `bids`/`offers` carry `px.value` and `qty`, and
`stats.lastPriceSample` reports `shortPx == 1 - longPx`. Settlement is a
single number for the long side (1 or 0). Tick sizes vary per market
(0.005 on MLB, 0.01 on tennis) and the minimum order is 0.01 contracts.

Verified live (public reads): sports/league slugs, event -> market -> sides
mapping, the book shape, settlement values. NOT yet verified, each needs
the 1-contract checks in docs/POLYMARKET_US_CLIENT_2026-10-09.md before the
first real order: BUY_SHORT price terms (assumed: the short contract's own
price, 1 - long), the sign of `netPositionDecimal` (assumed: short < 0),
and whether query strings belong in the signed path (assumed: no).

Fees (docs.polymarket.us/fees): taker Θ = 0.0695, maker Θ = -0.0125 (a
rebate), fee = Θ·C·p·(1-p), banker's-rounded to the cent per order. The
bot's edge math does NOT count the maker rebate as edge (maker fee is
modelled as 0): a rebate on a fill that adverse selection made is still a
loss, and the pilot is what measures that.

Rate limit: 25 req/s per IP across all endpoints. This client paces itself
to 20/s. A 429 is never retried on order placement; GETs retry once after
1 s. The venue also rejects orders it cannot process within 5 s with
"Global Rate Limit Exceeded" — a reject, not a fill, and never resubmitted
blindly: the executor treats REJECTED as final.
"""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Optional

import httpx

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

GATEWAY = "https://gateway.polymarket.us"
API = "https://api.polymarket.us"
USER_AGENT = "sportsbot/1.0 (+polymarket-us)"

TAKER_THETA = 0.0695
MAKER_THETA = -0.0125          # negative: the maker is paid

# Sport key -> (gateway sport slug, league slugs whose match-winner markets
# the scanner prices). Leagues verified in /v2/sports on 2026-10-09; the
# config may override them (`polymarket_us.leagues`).
SPORT_SLUG = {"tennis": "tennis", "baseball": "baseball", "table_tennis": "table-tennis"}
DEFAULT_LEAGUES: dict[str, tuple[str, ...]] = {
    "tennis": ("atp", "wta"),
    "baseball": ("mlb",),
    # Setka Cup / Czech Liga Pro: the fast leagues with documented fixing
    # risk; the strategy's table-tennis overrides stay deliberately strict.
    "table_tennis": ("setkameua", "setkamecz", "setkamemd", "czechligapro", "wtt"),
}
SPORT_FOR_KEY = {"tennis": Sport.TENNIS, "baseball": Sport.BASEBALL,
                 "table_tennis": Sport.TABLE_TENNIS}
# Match-winner market types seen live: tennis_match_winner,
# table_tennis_match_winner, baseball_team_full_game_winner; NFL rows in
# /v1/markets still carry the legacy "moneyline".
WINNER_SUFFIXES = ("_match_winner", "_full_game_winner")

STATE_MAP = {
    "ORDER_STATE_NEW": OrderStatus.OPEN,
    "ORDER_STATE_PENDING_NEW": OrderStatus.OPEN,
    "ORDER_STATE_PENDING_REPLACE": OrderStatus.OPEN,
    "ORDER_STATE_PENDING_CANCEL": OrderStatus.OPEN,
    "ORDER_STATE_PENDING_RISK": OrderStatus.OPEN,
    "ORDER_STATE_PARTIALLY_FILLED": OrderStatus.PARTIAL,
    "ORDER_STATE_FILLED": OrderStatus.FILLED,
    "ORDER_STATE_CANCELED": OrderStatus.CANCELED,
    "ORDER_STATE_EXPIRED": OrderStatus.CANCELED,
    "ORDER_STATE_REPLACED": OrderStatus.CANCELED,
    "ORDER_STATE_REJECTED": OrderStatus.REJECTED,
}
TIF = {OrderType.LIMIT: "TIME_IN_FORCE_GOOD_TILL_CANCEL",
       OrderType.IOC: "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
       OrderType.FOK: "TIME_IN_FORCE_FILL_OR_KILL"}


# ------------------------------------------------------------------ fees
def polymarket_us_taker_fee(price: float, shares: float, market_id: str | None = None,
                            theta: float = TAKER_THETA) -> float:
    """Marginal taker fee in dollars: Θ·p·(1-p)·shares (linear; the venue
    rounds the whole order to the cent — see `polymarket_us_order_fee`).
    Same (price, shares, market_id=None) shape as every other fee_fn."""
    return theta * price * (1.0 - price) * shares


def polymarket_us_order_fee(price: float, shares: float, theta: float = TAKER_THETA) -> float:
    """What the venue actually books for one order: banker's rounding to
    $0.01 of the exact fee. Negative for makers (a rebate). Matches the
    documented examples: 1,000 @ 0.10 -> taker 6.26 / maker -1.12;
    1,000 @ 0.50 -> 17.38 / -3.12."""
    exact = Decimal(str(theta)) * Decimal(str(shares)) * Decimal(str(price)) * (1 - Decimal(str(price)))
    return float(exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


# ---------------------------------------------------------------- client
class PolymarketUSClient(ExchangeClient):
    exchange = Exchange.POLYMARKET_US

    def __init__(self, key_id: Optional[str] = None, secret_key: Optional[str] = None,
                 leagues: Optional[dict] = None, include_live: bool = False,
                 timeout: float = 15.0, transport: Any = None,
                 max_rps: float = 20.0) -> None:
        self.key_id = key_id or os.environ.get("POLYMARKET_US_KEY_ID") or None
        self._secret = secret_key or os.environ.get("POLYMARKET_US_SECRET_KEY") or None
        self._key = None
        self.leagues = {k: tuple(v) for k, v in (leagues or DEFAULT_LEAGUES).items()}
        self.include_live = include_live
        self._http = httpx.Client(timeout=timeout, transport=transport,
                                  headers={"User-Agent": USER_AGENT})
        self._min_gap = 1.0 / max_rps if max_rps > 0 else 0.0
        self._last = 0.0
        self._lock = threading.Lock()

    # ----------------------------------------------------------- plumbing
    def _pace(self) -> None:
        with self._lock:
            wait = self._min_gap - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def _private_key(self):
        if self._key is None:
            if not self._secret or not self.key_id:
                raise RuntimeError("POLYMARKET_US_KEY_ID / POLYMARKET_US_SECRET_KEY not set — "
                                   "trading unavailable (public reads still work)")
            from cryptography.hazmat.primitives.asymmetric import ed25519

            raw = base64.b64decode(self._secret)
            self._key = ed25519.Ed25519PrivateKey.from_private_bytes(raw[:32])
        return self._key

    def auth_headers(self, method: str, path: str) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        sig = self._private_key().sign(f"{ts}{method.upper()}{path}".encode())
        return {"X-PM-Access-Key": self.key_id or "", "X-PM-Timestamp": ts,
                "X-PM-Signature": base64.b64encode(sig).decode(),
                "Content-Type": "application/json"}

    def _request(self, method: str, url: str, params: dict | None = None,
                 json: Any = None, auth: bool = False, retry: bool = True) -> Any:
        path = url[len(API):] if url.startswith(API) else url[len(GATEWAY):]
        headers = self.auth_headers(method, path) if auth else {}
        for attempt in range(2):
            self._pace()
            resp = self._http.request(method, url, params=params, json=json, headers=headers)
            if resp.status_code == 429 and retry and method == "GET" and attempt == 0:
                time.sleep(1.0)             # documented: wait >= 1 s, then back off
                continue
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        resp.raise_for_status()
        return {}

    @staticmethod
    def _ts(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _amount(a: Any) -> Optional[float]:
        if isinstance(a, dict):
            a = a.get("value")
        if a in (None, ""):
            return None
        return float(Decimal(str(a)))

    # ---------------------------------------------------------- discovery
    def list_sports_markets(self, sport_tag: str) -> list[MarketInfo]:
        slug = SPORT_SLUG.get(sport_tag)
        leagues = self.leagues.get(sport_tag)
        if not slug or not leagues:
            raise ValueError(f"unknown sport {sport_tag!r}; known: {sorted(SPORT_SLUG)}")
        sport = SPORT_FOR_KEY[sport_tag]
        out: list[MarketInfo] = []
        offset = 0
        while True:
            data = self._request("GET", f"{GATEWAY}/v2/sports/{slug}/events",
                                 params={"limit": 100, "offset": offset})
            events = data.get("events", []) or []
            for ev in events:
                tags = {t.get("slug") for t in ev.get("tags", []) or []}
                league = next((lg for lg in leagues if lg in tags), None)
                if league is None or ev.get("ended") or ev.get("closed"):
                    continue
                if ev.get("live") and not self.include_live:
                    continue
                ordering = next((t.get("league", {}).get("ordering")
                                 for t in ev.get("tags", []) or []
                                 if isinstance(t.get("league"), dict)), None)
                for m in ev.get("markets", []) or []:
                    info = self._to_market_info(m, ev, sport, league, ordering)
                    if info is not None:
                        out.append(info)
            if len(events) < 100:
                break
            offset += 100
        return out

    def _to_market_info(self, m: dict, ev: dict, sport: Sport, league: str,
                        ordering: Optional[str]) -> Optional[MarketInfo]:
        mtype = str(m.get("sportsMarketType") or "")
        if not (mtype == "moneyline" or mtype.endswith(WINNER_SUFFIXES)):
            return None
        if m.get("status") != "MARKET_STATUS_OPEN":
            return None
        sides = m.get("marketSides") or []
        long_side = next((s for s in sides if s.get("long")), None)
        short_side = next((s for s in sides if not s.get("long")), None)
        if not long_side or not short_side:
            return None
        home, away = long_side.get("description"), short_side.get("description")
        if not home or not away or home == away:
            return None
        # Leagues list "away" or "home" first; the long side is always the
        # first-listed competitor. Home field (baseball's model advantage)
        # is therefore the OTHER side when the league lists the away team
        # first (MLB does).
        home_field = away if ordering == "away" else home
        return MarketInfo(
            exchange=Exchange.POLYMARKET_US,
            market_id=m["slug"],
            question=ev.get("title") or m.get("question") or "",
            slug=m["slug"],
            sport=sport,
            home=home,
            away=away,
            start_time=self._ts(ev.get("startTime") or ev.get("startDate")),
            close_time=self._ts(m.get("endDate")),
            active=True,
            tick_size=float(m.get("orderPriceMinTickSize") or 0.01),
            min_order_size=float(m.get("minimumTradeQty") or 1.0),
            meta={"event_slug": ev.get("slug"), "league": league,
                  "home_field": home_field, "live": bool(ev.get("live")),
                  "fee_coefficient": m.get("feeCoefficient"),
                  "market_type": mtype,
                  "team_ids": [long_side.get("teamId"), short_side.get("teamId")]},
        )

    def get_quote(self, market: MarketInfo) -> MarketQuote:
        data = self._request("GET", f"{GATEWAY}/v1/markets/{market.market_id}/book")
        md = data.get("marketData") or {}
        bids = sorted((BookLevel(price=self._amount(e.get("px")), size=float(e.get("qty") or 0))
                       for e in md.get("bids") or [] if self._amount(e.get("px")) is not None),
                      key=lambda lvl: -lvl.price)
        asks = sorted((BookLevel(price=self._amount(e.get("px")), size=float(e.get("qty") or 0))
                       for e in md.get("offers") or [] if self._amount(e.get("px")) is not None),
                      key=lambda lvl: lvl.price)
        return MarketQuote(market_id=market.market_id,
                           bid=bids[0].price if bids else None,
                           ask=asks[0].price if asks else None,
                           bids=bids, asks=asks)

    def get_resolution(self, market_id: str) -> Optional[bool]:
        try:
            data = self._request("GET", f"{GATEWAY}/v1/markets/{market_id}/settlement")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None                 # "not found or not settled"
            log.error("polymarket_us settlement %s: %s", market_id, exc)
            return None
        except httpx.HTTPError as exc:
            log.error("polymarket_us settlement %s: %s", market_id, exc)
            return None
        v = data.get("settlement")
        if v is None:
            return None
        v = float(v)
        return True if v >= 0.999 else False if v <= 0.001 else None

    # ------------------------------------------------------------ trading
    @staticmethod
    def _intent(side: Side, buy: bool = True) -> str:
        return {(Side.YES, True): "ORDER_INTENT_BUY_LONG",
                (Side.NO, True): "ORDER_INTENT_BUY_SHORT",
                (Side.YES, False): "ORDER_INTENT_SELL_LONG",
                (Side.NO, False): "ORDER_INTENT_SELL_SHORT"}[(side, buy)]

    def place_order(self, order: Order) -> Order:
        """Limit/IOC/FOK buy of `order.side` at `order.price` (that side's own
        price: a NO order carries the NO price, as everywhere in the bot).
        `order.post_only` maps to `participateDontInitiate`, so a maker
        order that would cross is rejected by the venue rather than
        filled as a taker. Never retried on error."""
        body: dict[str, Any] = {
            "marketSlug": order.market_id,
            "type": "ORDER_TYPE_LIMIT",
            "intent": self._intent(order.side, buy=True),
            "price": {"value": f"{order.price:.4f}", "currency": "USD"},
            "quantity": round(order.size, 2),
            "tif": TIF.get(order.order_type, TIF[OrderType.LIMIT]),
            "participateDontInitiate": bool(order.post_only),
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        }
        try:
            data = self._request("POST", f"{API}/v1/orders", json=body, auth=True, retry=False)
            order.order_id = str(data.get("id") or "")
            order.raw = {"request": body, "response": data}
            if not order.order_id:
                order.status = OrderStatus.REJECTED
                return order
            # The create response carries no state unless the call was
            # synchronous; read it back so an immediate fill is booked now.
            venue = self.get_order(order.order_id)
            if venue is not None:
                order.filled = venue.filled
                order.status = venue.status
            else:
                order.status = OrderStatus.OPEN       # reconciled next cycle
        except httpx.HTTPStatusError as exc:
            log.error("polymarket_us order rejected: %s %s", exc.response.status_code,
                      exc.response.text[:300])
            order.status = OrderStatus.REJECTED
            order.raw = {"request": body, "error": exc.response.text[:500]}
        except Exception as exc:  # surface, never crash the loop, never resubmit
            log.error("polymarket_us order failed: %s", exc)
            order.status = OrderStatus.REJECTED
            order.raw = {"request": body, "error": str(exc)}
        return order

    def _parse_order(self, o: dict) -> Order:
        intent = str(o.get("intent") or "")
        outcome = str(o.get("outcomeSide") or "")
        side = (Side.YES if intent.endswith("_LONG") or outcome == "OUTCOME_SIDE_YES"
                else Side.NO)
        filled = float(o.get("cumQuantity") or 0.0)
        return Order(
            order_id=str(o.get("id") or ""),
            exchange=Exchange.POLYMARKET_US,
            market_id=o.get("marketSlug") or "",
            side=side,
            price=self._amount(o.get("price")) or 0.0,
            size=float(o.get("quantity") or 0.0),
            filled=filled,
            status=STATE_MAP.get(str(o.get("state") or ""),
                                 OrderStatus.PARTIAL if filled > 0 else OrderStatus.OPEN),
            raw=o,
        )

    def get_order(self, order_id: str) -> Optional[Order]:
        try:
            data = self._request("GET", f"{API}/v1/order/{order_id}", auth=True)
        except Exception as exc:
            log.error("polymarket_us get_order %s failed: %s", order_id, exc)
            return None
        o = data.get("order") if isinstance(data, dict) else None
        return self._parse_order(o) if o else None

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._request("POST", f"{API}/v1/order/{order_id}/cancel", json={},
                          auth=True, retry=False)
            return True
        except Exception as exc:
            log.error("polymarket_us cancel %s failed: %s", order_id, exc)
            return False

    def get_open_orders(self) -> list[Order]:
        try:
            data = self._request("GET", f"{API}/v1/orders/open", auth=True)
        except Exception as exc:
            log.error("polymarket_us get_open_orders failed: %s", exc)
            return []
        return [self._parse_order(o) for o in data.get("orders", []) or []]

    def get_positions(self) -> list[Position]:
        out: list[Position] = []
        cursor = None
        for _ in range(50):
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            try:
                data = self._request("GET", f"{API}/v1/portfolio/positions", params=params, auth=True)
            except Exception as exc:
                log.error("polymarket_us positions failed: %s", exc)
                break
            for slug, p in (data.get("positions") or {}).items():
                net = float(p.get("netPositionDecimal") or p.get("netPosition") or 0.0)
                if net == 0.0 or p.get("expired"):
                    continue
                size = abs(net)
                cost = self._amount(p.get("cost")) or 0.0
                out.append(Position(
                    market_id=(p.get("marketMetadata") or {}).get("slug") or slug,
                    side=Side.YES if net > 0 else Side.NO,   # sign convention: unverified
                    size=size,
                    avg_price=round(abs(cost) / size, 4) if size else 0.0,
                    realized_pnl=self._amount(p.get("realized")) or 0.0,
                ))
            cursor = data.get("nextCursor")
            if data.get("eof", True) or not cursor:
                break
        return out

    def get_balance(self) -> float:
        try:
            data = self._request("GET", f"{API}/v1/account/balances", auth=True)
        except Exception as exc:
            log.error("polymarket_us balances failed: %s", exc)
            return 0.0
        for b in data.get("balances", []) or []:
            if str(b.get("currency") or "USD").upper() == "USD":
                v = b.get("buyingPower")
                if v is None:
                    v = b.get("currentBalance")
                return float(v or 0.0)
        return 0.0

    def close_position(self, market_id: str, side: Side, quote: MarketQuote,
                       min_price: float = 0.02) -> Optional[dict]:
        """Sell the WHOLE position in a market (the venue's close-position
        order has no quantity). Refuses when the exit side of the book has
        collapsed below `min_price`, so a position is never dumped into an
        empty book; the caller holds and retries."""
        exit_px = quote.bid if side == Side.YES else (
            None if quote.ask is None else round(1.0 - quote.ask, 6))
        if exit_px is None or exit_px < min_price:
            return None
        body = {"marketSlug": market_id, "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
                "synchronousExecution": True, "maxBlockTime": "5"}
        try:
            data = self._request("POST", f"{API}/v1/order/close-position", json=body,
                                 auth=True, retry=False)
        except Exception as exc:
            log.error("polymarket_us close_position %s failed: %s", market_id, exc)
            return None
        order_id = str(data.get("id") or "")
        venue = self.get_order(order_id) if order_id else None
        if venue is None or venue.filled <= 0:
            return None
        raw = venue.raw or {}
        avg = self._amount(raw.get("avgPx")) or exit_px
        fee = self._amount(raw.get("commissionNotionalTotalCollected")) or 0.0
        return {"closed_size": venue.filled, "avg_price": avg,
                "proceeds": round(venue.filled * avg - fee, 4), "fee": fee,
                "order_id": order_id}
