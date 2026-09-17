"""The Odds API client — sportsbook h2h odds for consensus fair value.

Docs: https://the-odds-api.com/liveapi/guides/v4/
Free tier: 500 requests/month; each sport+region request costs 1.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from ..http_util import retrying_session

log = logging.getLogger(__name__)

BASE = "https://api.the-odds-api.com/v4"


@dataclass
class Game:
    sport_key: str
    home_team: str
    away_team: str
    commence_time: datetime
    # per book: {team_name: decimal_odds}; may include "Draw" for soccer
    book_odds: list[dict[str, float]]


class OddsApiClient:
    def __init__(self, api_key: str, regions: str = "us", cache_ttl_sec: float = 3600):
        self.api_key = api_key
        self.regions = regions
        self.cache_ttl_sec = cache_ttl_sec
        self.http = retrying_session()
        # sport_key -> (fetched_at_monotonic, games); every request costs quota
        # (free tier: 500/month), so odds are reused across scan cycles until stale
        self._cache: dict[str, tuple[float, list[Game]]] = {}

    def h2h_games(self, sport_key: str) -> list[Game]:
        if not self.api_key:
            log.warning("ODDS_API_KEY not set — skipping odds feed for %s", sport_key)
            return []
        cached = self._cache.get(sport_key)
        if cached and time.monotonic() - cached[0] < self.cache_ttl_sec:
            return cached[1]
        r = self.http.get(
            f"{BASE}/sports/{sport_key}/odds",
            params={
                "apiKey": self.api_key,
                "regions": self.regions,
                "markets": "h2h",
                "oddsFormat": "decimal",
            },
            timeout=30,
        )
        if r.status_code == 401:
            log.error("The Odds API rejected the key (401)")
            self._cache[sport_key] = (time.monotonic(), [])
            return []
        r.raise_for_status()
        remaining = r.headers.get("x-requests-remaining")
        if remaining:
            log.info("odds-api requests remaining: %s", remaining)

        games: list[Game] = []
        for g in r.json():
            book_odds = []
            for bk in g.get("bookmakers", []):
                for mkt in bk.get("markets", []):
                    if mkt.get("key") != "h2h":
                        continue
                    odds = {o["name"]: float(o["price"]) for o in mkt.get("outcomes", [])}
                    if odds:
                        book_odds.append(odds)
            games.append(
                Game(
                    sport_key=sport_key,
                    home_team=g.get("home_team", ""),
                    away_team=g.get("away_team", ""),
                    commence_time=datetime.fromisoformat(
                        g["commence_time"].replace("Z", "+00:00")
                    ).astimezone(timezone.utc),
                    book_odds=book_odds,
                )
            )
        log.info("odds-api: %d games for %s", len(games), sport_key)
        self._cache[sport_key] = (time.monotonic(), games)
        return games
