"""Polymarket US: the CFTC-regulated US exchange (QCEX), separate from the
main Polymarket CLOB. US-legal live venue alongside Kalshi.

Measured 2026-10-13 against the public gateway and the official docs
(docs.polymarket.us, SDK `polymarket-us` 2.4.1; the SDK is not imported
here, the wire format is implemented directly so dry-run and paper need
nothing installed):

* Public, keyless (`gateway.polymarket.us`): `/v1/sports` (league codes ->
  series ids), `/v1/events?seriesId=` (events with nested markets; an NHL
  game carries ~183 markets, exactly one `SPORTS_MARKET_TYPE_MONEYLINE`),
  `/v1/markets/{slug}/book`, `/v1/markets/{slug}/bbo` (top of book plus the
  cumulative `sharesTraded` and `lastTradePx`), `/v1/markets/{slug}/settlement`
  (`{"settlement": 1}` once the LONG side won; an error until settled).
* Authenticated (`api.polymarket.us`): orders, open orders, positions,
  balances. Headers `X-PM-Access-Key`, `X-PM-Timestamp` (ms) and
  `X-PM-Signature` = base64 Ed25519 signature of
  `f"{timestamp}{METHOD}{path}"` (path without the query string), with a
  base64 Ed25519 secret (32-byte seed, or 64 bytes of which the first 32).
* Every price is in YES (long) terms: buying NO at 0.40 is a
  `ORDER_INTENT_BUY_SHORT` at 0.60. YES = the long side = outcomes[0].
* `participateDontInitiate: true` is a real maker-only flag: the venue
  rejects the order instead of letting it cross. The executor sets
  `Order.post_only` for maker arms and this client sends it.
* Fees (docs/fees.md): taker 0.0695 x C x p x (1 - p) (table tennis
  0.10); makers pay NOTHING and today earn a 0.0125 x C x p x (1 - p)
  rebate, which from 2026-10-19 13:00 ET moves to volume tiers (zero below
  $10M a month). Here the maker fee is modelled as 0 and the rebate
  ignored -- conservative.
* No public trade tape: trades stream only over the authenticated
  markets websocket. `polymarket_us_tape` records it (`sportsbot us-tape`,
  keys needed). `recent_trades` reads that record and raises `TapeGap` for
  a window it does not cover, and with no record at all, so a resting paper
  order here never fills (no tape, no fill) and the live shadow stays absent
  instead of reading zero.
* Rate limit: 25 requests/s per IP; a 429 means wait >= 1 s. GETs retry
  with backoff on 429/5xx; order placement never retries.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from sportsbot.core.types import (
    Exchange,
    MarketInfo,
    MarketQuote,
    BookLevel,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Sport,
    TapePrint,
)
from sportsbot.data.teams import canonical, is_team_sport
from sportsbot.exchanges.base import ExchangeClient, TapeGap

log = logging.getLogger(__name__)

GATEWAY = "https://gateway.polymarket.us"
API = "https://api.polymarket.us"
ENV_KEY_ID = "POLYMARKET_US_KEY_ID"
ENV_SECRET = "POLYMARKET_US_SECRET_KEY"

TAKER_THETA = 0.0695
TAKER_THETA_TABLE_TENNIS = 0.10
MONEYLINE = "SPORTS_MARKET_TYPE_MONEYLINE"

# Our sport keys -> the venue's league codes (`sport` in /v1/sports).
# Soccer is absent on purpose: on 2026-10-13 the UCL events carried no
# SPORTS_MARKET_TYPE_MONEYLINE market (three-way results are typed
# differently); add it once its result markets are mapped and tested.
LEAGUES: dict[str, tuple[str, ...]] = {
    "baseball": ("mlb",),
    "hockey": ("nhl",),
    "football": ("nfl",),
    "basketball": ("nba",),
    "tennis": ("atp", "wta"),
    "college_football": ("cfb",),
    "esports": ("lol", "cs2", "dota2", "valorant"),
    "mma": ("ufc",),
}
# Config sport keys this venue does not carry as moneylines (see LEAGUES).
UNSUPPORTED = ("soccer", "cricket", "table_tennis")
SPORT_FOR_KEY = {
    "baseball": Sport.BASEBALL, "hockey": Sport.HOCKEY, "football": Sport.FOOTBALL,
    "basketball": Sport.BASKETBALL, "tennis": Sport.TENNIS,
    "college_football": Sport.COLLEGE_FOOTBALL, "esports": Sport.ESPORTS, "mma": Sport.MMA,
}
STALE_GAME_HOURS = 8
LOOKAHEAD_DAYS = 7

_TIF = {
    OrderType.LIMIT: "TIME_IN_FORCE_GOOD_TILL_CANCEL",
    OrderType.LIMIT_GTD: "TIME_IN_FORCE_GOOD_TILL_CANCEL",
    OrderType.FOK: "TIME_IN_FORCE_FILL_OR_KILL",
    OrderType.IOC: "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
}
_STATE = {
    "ORDER_STATE_NEW": OrderStatus.OPEN,
    "ORDER_STATE_PENDING_NEW": OrderStatus.PENDING,
    "ORDER_STATE_PENDING_REPLACE": OrderStatus.OPEN,
    "ORDER_STATE_PENDING_CANCEL": OrderStatus.OPEN,
    "ORDER_STATE_PENDING_RISK": OrderStatus.PENDING,
    "ORDER_STATE_PARTIALLY_FILLED": OrderStatus.PARTIAL,
    "ORDER_STATE_FILLED": OrderStatus.FILLED,
    "ORDER_STATE_CANCELED": OrderStatus.CANCELED,
    "ORDER_STATE_REPLACED": OrderStatus.OPEN,
    "ORDER_STATE_REJECTED": OrderStatus.REJECTED,
    "ORDER_STATE_EXPIRED": OrderStatus.CANCELED,
}


def taker_fee_us(price: float, shares: float, sport: Optional[str] = None) -> float:
    theta = TAKER_THETA_TABLE_TENNIS if sport == "table_tennis" else TAKER_THETA
    p = min(max(float(price), 0.0), 1.0)
    return round(theta * float(shares) * p * (1.0 - p), 6)


def sign_headers(key_id: str, secret_b64: str, method: str, path: str,
                 now_ms: Optional[int] = None) -> dict[str, str]:
    """The venue's Ed25519 request signature (see module docstring)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    ts = str(int(time.time() * 1000) if now_ms is None else now_ms)
    raw = base64.b64decode(secret_b64)
    seed = raw[:32] if len(raw) == 64 else raw
    key = Ed25519PrivateKey.from_private_bytes(seed)
    sig = key.sign(f"{ts}{method.upper()}{path}".encode())
    return {"X-PM-Access-Key": key_id, "X-PM-Timestamp": ts,
            "X-PM-Signature": base64.b64encode(sig).decode()}


def _dt(s: Any) -> Optional[datetime]:
    if not s:
        return None
    # The bbo stamps carry nanoseconds; Python 3.10's fromisoformat takes at
    # most six fractional digits.
    text = re.sub(r"(\.\d{6})\d+", r"\1", str(s).replace("Z", "+00:00"))
    try:
        d = datetime.fromisoformat(text)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _px(amount: Any) -> Optional[float]:
    if isinstance(amount, dict):
        amount = amount.get("value")
    try:
        return float(amount)
    except (TypeError, ValueError):
        return None


def _json_list(v: Any) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        import json
        try:
            out = json.loads(v)
            return out if isinstance(out, list) else []
        except ValueError:
            return []
    return []


class PolymarketUSClient(ExchangeClient):
    exchange = Exchange.POLYMARKET_US

    def __init__(self, key_id: Optional[str] = None, secret_key: Optional[str] = None,
                 http: Optional[httpx.Client] = None, timeout: float = 30.0, tape: Any = None) -> None:
        self.key_id = key_id if key_id is not None else os.environ.get(ENV_KEY_ID, "")
        self.secret_key = secret_key if secret_key is not None else os.environ.get(ENV_SECRET, "")
        self.http = http or httpx.Client(timeout=timeout, headers={
            "User-Agent": "sportsbot/1.0", "Accept": "application/json"})
        self._series: Optional[dict[str, dict]] = None
        self._slug_by_order: dict[str, str] = {}
        self._tape_store: Any = tape if tape is not None else False   # False = look it up on first use

    # ------------------------------------------------------------------ http
    @property
    def has_credentials(self) -> bool:
        return bool(self.key_id and self.secret_key)

    def _get(self, path: str, params: Optional[dict] = None, auth: bool = False) -> Any:
        """GET with backoff on 429/5xx (GETs only; orders never retry)."""
        base = API if auth else GATEWAY
        for attempt, pause in enumerate((1.0, 2.0, 4.0, None)):
            headers = sign_headers(self.key_id, self.secret_key, "GET", path) if auth else {}
            resp = self.http.get(f"{base}{path}", params=params, headers=headers)
            if resp.status_code in (429, 500, 502, 503, 504) and pause is not None:
                time.sleep(pause)
                continue
            resp.raise_for_status()
            return resp.json()
        return None

    def _post_once(self, path: str, body: dict) -> Any:
        """One authenticated POST, never retried (order placement / cancel)."""
        headers = {"Content-Type": "application/json",
                   **sign_headers(self.key_id, self.secret_key, "POST", path)}
        resp = self.http.post(f"{API}{path}", json=body, headers=headers)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # ------------------------------------------------------------- discovery
    def _series_ids(self) -> dict[str, dict]:
        if self._series is None:
            data = self._get("/v1/sports") or {}
            self._series = {str(s.get("sport")): s for s in data.get("sports", [])
                            if s.get("isOperational", True)}
        return self._series

    def list_sports_markets(self, sport_tag: str) -> list[MarketInfo]:
        leagues = LEAGUES.get(sport_tag)
        if leagues is None:
            if sport_tag in UNSUPPORTED:
                return []                    # a sport this venue does not list as moneylines
            raise ValueError(f"unknown sport key {sport_tag!r} for Polymarket US; known: {sorted(LEAGUES)}")
        sport = SPORT_FOR_KEY[sport_tag]
        series = self._series_ids()
        now = datetime.now(timezone.utc)
        window = {"startTimeMin": (now - timedelta(hours=STALE_GAME_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "startTimeMax": (now + timedelta(days=LOOKAHEAD_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        out: list[MarketInfo] = []
        seen: set[str] = set()
        for league in leagues:
            entry = series.get(league)
            sid = (entry or {}).get("series")
            if not sid:
                continue
            for offset in range(0, 1000, 100):
                data = self._get("/v1/events", {"seriesId": sid, "active": "true", "closed": "false",
                                                "limit": 100, "offset": offset, **window}) or {}
                events = data.get("events", [])
                for ev in events:
                    for m in self.moneylines_from_event(ev, sport, league, (entry or {}).get("ordering")):
                        if m.market_id not in seen:
                            seen.add(m.market_id)
                            out.append(m)
                if len(events) < 100:
                    break
        return out

    @staticmethod
    def moneylines_from_event(ev: dict, sport: Sport, league: str,
                              ordering: Optional[str] = None) -> list[MarketInfo]:
        out = []
        now = datetime.now(timezone.utc)
        for m in ev.get("markets") or []:
            if m.get("sportsMarketTypeV2") != MONEYLINE:
                continue
            if m.get("closed") or not m.get("active", True):
                continue
            start = _dt(m.get("gameStartTime") or ev.get("startTime"))
            if start is not None and now - start > timedelta(hours=STALE_GAME_HOURS):
                continue
            outcomes = [str(o) for o in _json_list(m.get("outcomes"))]
            if len(outcomes) != 2:
                continue
            sides = m.get("marketSides") or []
            long_side = next((s for s in sides if s.get("long") is True), None)
            short_side = next((s for s in sides if s.get("long") is False), None)
            names = []
            for s, fallback in ((long_side, outcomes[0]), (short_side, outcomes[1])):
                team = (s or {}).get("team") or {}
                names.append((fallback, str(team.get("safeName") or ""), team.get("ordering")))
            home_name, away_name = outcomes[0], outcomes[1]
            meta: dict[str, Any] = {
                "event_slug": ev.get("slug"), "league": league,
                "fee_coefficient": m.get("feeCoefficient"),
                "outcome_names": outcomes,
            }
            if is_team_sport(sport):
                c0 = canonical(sport, names[0][0]) or canonical(sport, names[0][1])
                c1 = canonical(sport, names[1][0]) or canonical(sport, names[1][1])
                if not c0 or not c1 or c0 == c1:
                    continue                      # conservative: unknown names are skipped
                home_name, away_name = c0, c1
            elif sport == Sport.COLLEGE_FOOTBALL:
                home_name = names[0][1] or outcomes[0]
                away_name = names[1][1] or outcomes[1]
            if sport == Sport.BASEBALL:
                # The side the venue flags as home (each side's team carries
                # it); outcomes[0] is the LONG side, not necessarily home.
                flagged = [n for n, _, o in names if o == "home"]
                if flagged:
                    meta["home_field"] = flagged[0]
                elif ordering == "away":
                    meta["home_field"] = outcomes[1]
            out.append(MarketInfo(
                exchange=Exchange.POLYMARKET_US,
                market_id=str(m.get("slug")),
                yes_token_id=f"{m.get('slug')}#long",
                no_token_id=f"{m.get('slug')}#short",
                question=str(m.get("question") or ev.get("title") or ""),
                slug=str(m.get("slug")),
                sport=sport,
                home=home_name,
                away=away_name,
                start_time=start,
                close_time=_dt(m.get("endDate") or ev.get("endDate")),
                active=True,
                tick_size=float(m.get("orderPriceMinTickSize") or 0.01),
                min_order_size=float(m.get("minimumTradeQty") or 1.0),
                neg_risk=False,
                meta=meta,
            ))
        return out

    def get_quote(self, market: MarketInfo) -> MarketQuote:
        data = (self._get(f"/v1/markets/{market.market_id}/book") or {}).get("marketData") or {}

        def levels(rows: list, reverse: bool) -> list[BookLevel]:
            out = []
            for r in rows or []:
                p, q = _px(r.get("px")), _px(r.get("qty"))
                if p is None or q is None or q <= 0:
                    continue
                out.append(BookLevel(price=p, size=q))
            return sorted(out, key=lambda b: b.price, reverse=reverse)

        bids = levels(data.get("bids"), True)
        asks = levels(data.get("offers"), False)
        return MarketQuote(market_id=market.market_id,
                           bid=bids[0].price if bids else None,
                           ask=asks[0].price if asks else None,
                           bids=bids, asks=asks)

    def get_bbo(self, slug: str) -> dict:
        """Public top of book plus the venue's running trade counters: the
        closest thing to a tape it publishes without keys. `traded` is the
        cumulative `sharesTraded`, `last_px` the last print (long frame)."""
        d = (self._get(f"/v1/markets/{slug}/bbo") or {}).get("marketData") or {}
        traded = d.get("sharesTraded")
        try:
            traded = float(traded) if traded is not None else None
        except (TypeError, ValueError):
            traded = None
        return {"bid": _px(d.get("bestBid")), "ask": _px(d.get("bestAsk")),
                "last_px": _px(d.get("lastTradePx")), "traded": traded,
                "open_interest": _px(d.get("openInterest")),
                "last_ts": _dt((d.get("lastPriceSample") or {}).get("ts")),
                "state": d.get("state")}

    def recent_trades(self, market: MarketInfo, since: datetime) -> list[TapePrint]:
        """Prints from the recorded websocket tape (`polymarket_us_tape`), YES
        frame, newest first. No record, or a window the record does not fully
        cover, raises `TapeGap`: callers then record nothing (the paper venue
        fills nothing, the live shadow stays absent) rather than read a
        missing tape as "no prints". Returning [] here once wrote
        `tape_through: 0.0` on every order, a guess stored as data."""
        store = self._tape()
        if store is None:
            raise TapeGap("no Polymarket US tape recorded (run `sportsbot us-tape`)")
        t0 = since.timestamp()
        if not store.covered(market.market_id, t0):
            raise TapeGap(f"no complete Polymarket US tape for {market.market_id} since {since.isoformat()}")
        return store.prints(market.market_id, t0)

    def _tape(self):
        if self._tape_store is False:
            from sportsbot.exchanges.polymarket_us_tape import default_store

            self._tape_store = default_store()
        return self._tape_store

    def get_resolution(self, market_id: str) -> Optional[bool]:
        try:
            data = self._get(f"/v1/markets/{market_id}/settlement")
        except httpx.HTTPError:
            return None
        if not isinstance(data, dict) or "settlement" not in data:
            return None
        try:
            v = float(data["settlement"])
        except (TypeError, ValueError):
            return None
        if v >= 0.999:
            return True
        if v <= 0.001:
            return False
        return None

    # --------------------------------------------------------------- trading
    def order_body(self, order: Order) -> dict:
        """The create-order request for `order` (engine semantics: BUY
        `order.side` at `order.price`, that side's own price)."""
        if order.side == Side.YES:
            intent, yes_price = "ORDER_INTENT_BUY_LONG", float(order.price)
        else:
            intent, yes_price = "ORDER_INTENT_BUY_SHORT", round(1.0 - float(order.price), 4)
        body = {
            "marketSlug": order.market_id,
            "intent": intent,
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": f"{yes_price:.4f}", "currency": "USD"},
            "quantity": round(float(order.size), 2),
            "tif": _TIF[order.order_type],
            "participateDontInitiate": bool(getattr(order, "post_only", False)),
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        }
        # Venue-enforced expiry (EDGE_VERDICT Result 23): GTD + goodTillTime.
        # The docs type it only as a string (date-time on responses); sent as
        # RFC 3339 UTC, to be confirmed on the first live order.
        if order.order_type == OrderType.LIMIT_GTD and order.expires_at is not None:
            body["tif"] = "TIME_IN_FORCE_GOOD_TILL_DATE"
            body["goodTillTime"] = order.expires_at.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        return body

    def place_order(self, order: Order) -> Order:
        if not self.has_credentials:
            order.status = OrderStatus.REJECTED
            order.raw = {**(order.raw or {}), "error": f"{ENV_KEY_ID}/{ENV_SECRET} not set"}
            return order
        body = self.order_body(order)
        try:
            resp = self._post_once("/v1/orders", body)
        except Exception as exc:                       # noqa: BLE001 — never retried
            log.error("polymarket-us order failed: %s", exc)
            order.status = OrderStatus.REJECTED
            order.raw = {**(order.raw or {}), "error": str(exc)[:300]}
            return order
        order.order_id = str(resp.get("id") or "")
        if order.order_id:
            self._slug_by_order[order.order_id] = order.market_id
        filled, rejected = 0.0, None
        for ex in resp.get("executions") or []:
            kind = ex.get("type")
            if kind in ("EXECUTION_TYPE_FILL", "EXECUTION_TYPE_PARTIAL_FILL"):
                filled += _px(ex.get("lastShares")) or 0.0
            elif kind == "EXECUTION_TYPE_REJECTED":
                rejected = ex.get("orderRejectReason") or ex.get("text") or "rejected"
        order.filled = round(filled, 6)
        if rejected and filled <= 0:
            order.status = OrderStatus.REJECTED
        elif order.filled >= float(order.size) - 1e-9:
            order.status = OrderStatus.FILLED
        elif order.filled > 0:
            order.status = OrderStatus.PARTIAL
        else:
            order.status = OrderStatus.OPEN if order.order_id else OrderStatus.REJECTED
        order.raw = {**(order.raw or {}), "response": resp,
                     **({"reject_reason": str(rejected)[:200]} if rejected else {})}
        return order

    def cancel_order(self, order_id: str) -> bool:
        if not self.has_credentials:
            return False
        slug = self._slug_by_order.get(order_id)
        try:
            if not slug:
                detail = self._get(f"/v1/order/{order_id}", auth=True) or {}
                slug = (detail.get("order") or detail).get("marketSlug")
            if not slug:
                log.error("polymarket-us cancel: no market slug for %s", order_id)
                return False
            self._post_once(f"/v1/order/{order_id}/cancel", {"marketSlug": slug})
            return True
        except Exception as exc:                       # noqa: BLE001
            log.error("polymarket-us cancel failed for %s: %s", order_id, exc)
            return False

    @staticmethod
    def order_from_venue(o: dict) -> Order:
        intent = str(o.get("intent") or "")
        side = Side.NO if intent.endswith("_SHORT") else Side.YES
        yes_px = _px(o.get("price")) or 0.0
        return Order(
            order_id=str(o.get("id") or ""),
            exchange=Exchange.POLYMARKET_US,
            market_id=str(o.get("marketSlug") or ""),
            side=side,
            price=round(yes_px if side == Side.YES else 1.0 - yes_px, 6),
            size=float(_px(o.get("quantity")) or 0.0),
            filled=float(_px(o.get("cumQuantity")) or 0.0),
            status=_STATE.get(str(o.get("state")), OrderStatus.OPEN),
            raw={"venue": o},
        )

    def get_open_orders(self) -> list[Order]:
        if not self.has_credentials:
            return []
        try:
            data = self._get("/v1/orders/open", auth=True) or {}
        except Exception as exc:                       # noqa: BLE001
            log.error("polymarket-us get_open_orders failed: %s", exc)
            return []
        out = []
        for o in data.get("orders", []) or []:
            order = self.order_from_venue(o)
            if order.order_id and order.market_id:
                self._slug_by_order[order.order_id] = order.market_id
            out.append(order)
        return out

    def get_positions(self) -> list[Position]:
        if not self.has_credentials:
            return []
        try:
            data = self._get("/v1/portfolio/positions", auth=True) or {}
        except Exception as exc:                       # noqa: BLE001
            log.error("polymarket-us get_positions failed: %s", exc)
            return []
        out = []
        for slug, p in (data.get("positions") or {}).items():
            net = _px(p.get("netPosition")) or 0.0
            if abs(net) < 1e-9:
                continue
            avg = _px(p.get("avgPx")) or 0.0
            side = Side.YES if net > 0 else Side.NO
            out.append(Position(market_id=str(slug), side=side, size=abs(net),
                                avg_price=round(avg if side == Side.YES else 1.0 - avg, 6),
                                realized_pnl=_px(p.get("realized")) or 0.0))
        return out

    def get_balance(self) -> float:
        if not self.has_credentials:
            return 0.0
        try:
            data = self._get("/v1/account/balances", auth=True) or {}
        except Exception as exc:                       # noqa: BLE001
            log.error("polymarket-us get_balance failed: %s", exc)
            return 0.0
        for b in data.get("balances", []) or []:
            if str(b.get("currency", "USD")).upper() == "USD":
                return float(b.get("buyingPower", b.get("currentBalance", 0.0)) or 0.0)
        return 0.0
