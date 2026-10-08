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
STALE_EVENT_FLOOR_DAYS = 21   # Gamma startDate = listing time; postseason lists days ahead
STALE_GAME_HOURS = 8          # a game this long past first pitch is not on the slate

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


# Documented sports fee (docs.polymarket.com, "Sports Market Fees", read
# 2026-09-28): fee = C x feeRate x p x (1 - p), taker rate 0.05, makers pay
# nothing and receive a 15% rebate. Worked example on the page: 100 shares at
# $0.50 -> $1.25. Gamma's per-market feeSchedule agrees:
# {"rate": 0.05, "exponent": 1, "takerOnly": true, "rebateRate": 0.15}.
SPORTS_TAKER_FEE_RATE = 0.05
SPORTS_MAKER_REBATE = 0.15


def fee_schedule_rates(raw) -> tuple[float, float]:
    """(taker rate, maker rebate share) from a Gamma `feeSchedule`, which
    arrives as a dict or a JSON string: {"rate": 0.05, "exponent": 1,
    "takerOnly": true, "rebateRate": 0.15}. Missing or malformed -> the
    documented sports default and rebate."""
    sched = raw
    if isinstance(raw, str):
        try:
            sched = json.loads(raw)
        except ValueError:
            sched = None
    if not isinstance(sched, dict):
        return SPORTS_TAKER_FEE_RATE, SPORTS_MAKER_REBATE
    try:
        rate = float(sched.get("rate", SPORTS_TAKER_FEE_RATE))
        rebate = float(sched.get("rebateRate", SPORTS_MAKER_REBATE))
    except (TypeError, ValueError):
        return SPORTS_TAKER_FEE_RATE, SPORTS_MAKER_REBATE
    if not (0.0 <= rate <= 0.2):
        return SPORTS_TAKER_FEE_RATE, SPORTS_MAKER_REBATE
    return rate, rebate


def taker_fee(price: float, shares: float, market_id: str | None = None,
              fee_rate: float = SPORTS_TAKER_FEE_RATE) -> float:
    """Taker fee in dollars: fee_rate x p x (1 - p) x shares.

    Quadratic in price and symmetric about 0.5, where it peaks at 1.25 cents
    a share. This replaced 0.10 x min(p, 1-p) x shares — the earlier reading
    of Gamma's raw takerBaseFee=1000 — which charged 5 cents a share at 0.50,
    four times the documented fee, and so overstated the hurdle on every
    taker edge. Makers pay zero here (`takerOnly`), so maker legs must not
    call this.

    `market_id` is accepted (and ignored — the rate does not vary by market)
    so this matches the `fee_fn(price, shares, market_id=None)` contract.
    """
    return fee_rate * price * (1.0 - price) * shares


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
        # conditionId -> taker fee rate from Gamma's per-market feeSchedule,
        # filled in by discovery. Rates differ by category (checked live
        # 2026-10-08: MLB and ATP moneylines 0.05 with a 15% rebate, the
        # MLB champion future 0.03 with 25%), so one flat number is wrong
        # for part of the slate whichever it picks.
        self._fee_rates: dict[str, float] = {}

    def fee_rate_for(self, market_id: Optional[str]) -> float:
        """Taker fee rate for a discovered market; the documented sports
        default for one discovery has not seen (fail toward over-costing)."""
        return self._fee_rates.get(str(market_id or ""), SPORTS_TAKER_FEE_RATE)

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
        # Some long-stale events stay active=true, so a startDate floor keeps
        # the page count bounded — but Gamma's event startDate is the LISTING
        # time, not the game time. Floored at "yesterday" this dropped the
        # 2026-09-30 wild-card games, listed on Sep 26-28, and the sim scanned
        # nothing (measured 2026-09-30: four open, accepting, two-sided
        # moneylines on Gamma, zero from this client). The floor is now three
        # weeks back and staleness is judged per market on gameStartTime in
        # `_moneyline_markets_from_event`, which is the field that means it.
        start_min = (datetime.now(timezone.utc) - timedelta(days=STALE_EVENT_FLOOR_DAYS)).strftime("%Y-%m-%d")
        # Order by volume, NOT start date. A game day lists dozens of derivative
        # events per game (inning winners, first-five) that share the game's
        # startDate; ordered by startDate they fill the page ahead of the game
        # itself, and after the moneyline filter drops them the slate looks
        # empty. Measured 2026-09-28: by startDate this returned inning
        # winners and a finale six days out; by volume the first three rows
        # were that day's games at $59k, $33k and $13k. The empty-slate
        # conclusion in EDGE_VERDICT's venue section came from this bug.
        while True:
            events = self._get(
                f"{GAMMA_BASE}/events",
                params={
                    "tag_id": tag_id,
                    "active": "true",
                    "closed": "false",
                    "start_date_min": start_min,
                    "order": "volume24hr",
                    "ascending": "false",
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
            # A game that started more than STALE_GAME_HOURS ago and is still
            # "active" is a settlement straggler, not a slate entry.
            gst = _parse_dt(m.get("gameStartTime"))
            if gst is not None and (datetime.now(timezone.utc) - gst) > timedelta(hours=STALE_GAME_HOURS):
                continue
            outcomes = _parse_json_field(m.get("outcomes"))
            tokens = _parse_json_field(m.get("clobTokenIds"))
            if len(outcomes) != 2 or len(tokens) != 2:
                continue
            fee_rate, rebate_rate = fee_schedule_rates(m.get("feeSchedule"))
            meta = {
                "event_slug": ev.get("slug"),
                "game_id": m.get("gameId"),
                "taker_base_fee": m.get("takerBaseFee"),
                "fees_enabled": m.get("feesEnabled"),
                "fee_rate": fee_rate,
                "rebate_rate": rebate_rate,
                "outcome_prices": _parse_json_field(m.get("outcomePrices")),
            }
            self._fee_rates[str(m.get("conditionId", ""))] = fee_rate
            # Polymarket lists the VISITOR first on MLB moneylines: measured
            # against MLB Stats on 981 of 981 resolved games, 2026-07-16 to
            # 2026-09-27 (slug mlb-{away}-{home}-{date}). Without this the
            # scanner models outcomes[0] as the home side and applies the
            # home advantage to the wrong team on every game.
            if sport == Sport.BASEBALL:
                meta["home_field"] = str(outcomes[1])
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
                    meta=meta,
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

    def get_resolution(self, market_id: str) -> Optional[bool]:
        """YES (= outcomes[0] / tokens[0]) winner flag from the CLOB market.

        CLOB tokens are index-aligned with Gamma outcomes, so tokens[0] is
        the side we call YES. Returns None while unresolved.
        """
        try:
            m = self._get(f"{CLOB_BASE}/markets/{market_id}")
        except httpx.HTTPError:
            return None
        tokens = m.get("tokens") or []
        if len(tokens) != 2 or not any(t.get("winner") for t in tokens):
            return None
        return bool(tokens[0].get("winner"))

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
