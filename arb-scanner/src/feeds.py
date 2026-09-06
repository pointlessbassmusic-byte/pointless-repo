"""Market feeds for the arbitrage scanner: Polymarket (Gamma + CLOB) and Kalshi.

Both feeds normalize into BinaryMarket: a single YES/NO question with two-sided
quotes in probability space (0-1). Multi-outcome markets are split per outcome
on Polymarket; Kalshi markets are already binary.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .http_util import retrying_session

log = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"


@dataclass
class BinaryMarket:
    platform: str            # "polymarket" | "kalshi"
    market_id: str           # token_id (poly) or ticker (kalshi)
    question: str            # full question text incl. outcome, used for matching
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    volume: float
    close_time: datetime | None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if 0 < x < 1 else None


class PolymarketFeed:
    def __init__(self):
        self.http = retrying_session(allow_post=True)  # CLOB /prices is a read-only POST

    def _events(self, max_events: int) -> list[dict]:
        events, offset = [], 0
        while offset < max_events:
            r = self.http.get(f"{GAMMA_BASE}/events", params={
                "closed": "false", "active": "true", "limit": 100, "offset": offset,
                "order": "volume24hr", "ascending": "false",
            }, timeout=30)
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            events.extend(batch)
            if len(batch) < 100:
                break
            offset += 100
        return events

    def markets(self, max_events: int = 500, min_liquidity: float = 1000) -> list[BinaryMarket]:
        """One BinaryMarket per outcome token, with CLOB quotes for both sides."""
        raw = []  # (question, close, yes_token, no_token) — binary markets only
        for ev in self._events(max_events):
            title = ev.get("title", "")
            for m in ev.get("markets", []) or []:
                if m.get("closed") or not m.get("active"):
                    continue
                if float(m.get("liquidityNum") or m.get("liquidity") or 0) < min_liquidity:
                    continue
                try:
                    outcomes = json.loads(m.get("outcomes") or "[]")
                    tokens = json.loads(m.get("clobTokenIds") or "[]")
                except (ValueError, TypeError):
                    continue
                # binary markets only: outcome 0 = YES side, outcome 1 = NO side
                if len(outcomes) != 2 or len(tokens) != 2:
                    continue
                question = m.get("question", "") or title
                if [o.lower() for o in outcomes] != ["yes", "no"]:
                    # e.g. ["Team A", "Team B"]: phrase as a YES question about outcome 0
                    question = f"{question} {outcomes[0]}"
                raw.append((question, _parse_dt(m.get("endDate")), tokens[0], tokens[1],
                            float(m.get("volumeNum") or m.get("volume") or 0)))

        # batch CLOB quotes for every token (both sides of every market)
        all_tokens = [t for row in raw for t in (row[2], row[3])]
        quotes = self._clob_prices(all_tokens)

        out = []
        for question, close, yes_tok, no_tok, volume in raw:
            yq, nq = quotes.get(yes_tok, {}), quotes.get(no_tok, {})
            out.append(BinaryMarket(
                platform="polymarket", market_id=yes_tok, question=question,
                yes_bid=_f(yq.get("BUY")), yes_ask=_f(yq.get("SELL")),
                no_bid=_f(nq.get("BUY")), no_ask=_f(nq.get("SELL")),
                volume=volume, close_time=close,
            ))
        log.info("polymarket feed: %d binary markets", len(out))
        return out

    def _clob_prices(self, token_ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(token_ids), 250):
            chunk = token_ids[i:i + 250]
            body = [{"token_id": t, "side": s} for t in chunk for s in ("BUY", "SELL")]
            r = self.http.post(f"{CLOB_BASE}/prices", json=body, timeout=30)
            r.raise_for_status()
            out.update(r.json())
        return out


class KalshiFeed:
    def __init__(self):
        self.http = retrying_session()

    def markets(self, max_events: int = 20000) -> list[BinaryMarket]:
        out, seen_events, cursor = [], 0, None
        while seen_events < max_events:
            params: dict = {"limit": min(200, max_events - seen_events),
                            "status": "open", "with_nested_markets": "true"}
            if cursor:
                params["cursor"] = cursor
            r = self.http.get(f"{KALSHI_BASE}/events", params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            events = data.get("events", [])
            seen_events += len(events)
            for ev in events:
                ev_title = ev.get("title", "")
                for m in ev.get("markets") or []:
                    # a market's own title carries the specific threshold/outcome;
                    # yes_sub_title disambiguates within multi-market events
                    q = " ".join(filter(None, [ev_title, m.get("title", ""),
                                               m.get("yes_sub_title", "")]))
                    out.append(BinaryMarket(
                        platform="kalshi", market_id=m.get("ticker", ""), question=q,
                        yes_bid=_f(m.get("yes_bid_dollars")), yes_ask=_f(m.get("yes_ask_dollars")),
                        no_bid=_f(m.get("no_bid_dollars")), no_ask=_f(m.get("no_ask_dollars")),
                        volume=float(m.get("volume_fp") or m.get("volume") or 0),
                        close_time=_parse_dt(m.get("close_time")),
                    ))
            cursor = data.get("cursor")
            if not cursor or not events:
                break
        log.info("kalshi feed: %d markets from %d events", len(out), seen_events)
        return out
