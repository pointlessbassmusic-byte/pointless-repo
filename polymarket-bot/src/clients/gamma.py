"""Polymarket Gamma API client — market/event discovery (no auth needed).

Docs: https://docs.polymarket.com/  (Gamma markets API)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from ..http_util import retrying_session

log = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"

# Gamma tag slugs that mark sports markets
SPORTS_TAG_SLUGS = ["sports", "nfl", "nba", "mlb", "nhl", "soccer", "epl"]


@dataclass
class SportsMarket:
    condition_id: str
    question: str
    slug: str
    end_date: datetime | None
    liquidity: float
    volume: float
    outcomes: list[str]          # e.g. ["Yes", "No"] or ["Team A", "Team B"]
    outcome_prices: list[float]  # gamma's last-trade/mid prices, same order
    clob_token_ids: list[str]    # CLOB token id per outcome, same order
    event_title: str = ""
    game_start: datetime | None = None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _parse_market(m: dict, event_title: str = "", game_start: datetime | None = None) -> SportsMarket | None:
    try:
        outcomes = json.loads(m.get("outcomes") or "[]")
        prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
        token_ids = json.loads(m.get("clobTokenIds") or "[]")
    except (ValueError, TypeError):
        return None
    if not outcomes or len(outcomes) != len(token_ids):
        return None
    return SportsMarket(
        condition_id=m.get("conditionId", ""),
        question=m.get("question", ""),
        slug=m.get("slug", ""),
        end_date=_parse_dt(m.get("endDate")),
        liquidity=float(m.get("liquidityNum") or m.get("liquidity") or 0),
        volume=float(m.get("volumeNum") or m.get("volume") or 0),
        outcomes=outcomes,
        outcome_prices=prices,
        clob_token_ids=token_ids,
        event_title=event_title,
        game_start=game_start,
    )


class GammaClient:
    def __init__(self, session: requests.Session | None = None):
        self.http = session or retrying_session()

    def _get(self, path: str, **params) -> list | dict:
        r = self.http.get(f"{GAMMA_BASE}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def sports_events(self, tag_slug: str = "sports", limit: int = 100) -> list[dict]:
        """Active (not closed) events for a tag, paginated."""
        events, offset = [], 0
        while True:
            batch = self._get(
                "/events",
                tag_slug=tag_slug,
                closed="false",
                active="true",
                limit=limit,
                offset=offset,
                order="startDate",
                ascending="true",
            )
            if not batch:
                break
            events.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
            if offset >= 1000:  # sanity cap
                break
        return events

    def active_sports_markets(self) -> list[SportsMarket]:
        """Flatten sports events into per-market records with token ids."""
        out: list[SportsMarket] = []
        seen: set[str] = set()
        for ev in self.sports_events():
            title = ev.get("title", "")
            start = _parse_dt(ev.get("startDate"))
            for m in ev.get("markets", []) or []:
                if m.get("closed") or not m.get("active"):
                    continue
                sm = _parse_market(m, event_title=title, game_start=start)
                if sm and sm.condition_id and sm.condition_id not in seen:
                    seen.add(sm.condition_id)
                    out.append(sm)
        log.info("gamma: %d active sports markets", len(out))
        return out

    def resolutions(self, condition_ids: list[str]) -> dict[str, float]:
        """token_id -> resolved outcome (1.0/0.0) for closed markets.

        A closed market's outcomePrices collapse to ~1/0 per outcome; each price
        maps to the clob token at the same index.
        """
        out: dict[str, float] = {}
        for i in range(0, len(condition_ids), 20):
            chunk = condition_ids[i:i + 20]
            markets = self._get("/markets", condition_ids=chunk, closed="true")
            for m in markets if isinstance(markets, list) else []:
                try:
                    prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
                    token_ids = json.loads(m.get("clobTokenIds") or "[]")
                except (ValueError, TypeError):
                    continue
                for token_id, price in zip(token_ids, prices):
                    if price >= 0.99:
                        out[token_id] = 1.0
                    elif price <= 0.01:
                        out[token_id] = 0.0
        return out
