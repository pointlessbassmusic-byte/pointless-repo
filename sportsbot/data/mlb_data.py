"""MLB data from the official, free MLB Stats API (statsapi.mlb.com).

No API key required. Endpoints used:
  /api/v1/schedule?sportId=1&startDate=&endDate=&hydrate=probablePitcher
  /api/v1/teams?sportId=1
Terms: MLB data is for non-commercial individual use per MLB's copyright
notice — review before any commercial deployment.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

BASE = "https://statsapi.mlb.com/api/v1"


@dataclass
class GameResult:
    date: datetime
    home: str               # canonical team name, e.g. "los angeles dodgers"
    away: str
    home_score: int
    away_score: int
    home_sp: Optional[str] = None   # starting pitcher names when hydrated
    away_sp: Optional[str] = None

    @property
    def home_won(self) -> bool:
        return self.home_score > self.away_score


@dataclass
class UpcomingGame:
    game_id: int
    date: datetime
    home: str
    away: str
    home_sp: Optional[str] = None
    away_sp: Optional[str] = None
    meta: dict = field(default_factory=dict)


def normalize_team(name: str) -> str:
    return " ".join(name.strip().lower().split())


# The Stats API's CDN answered 406 Not Acceptable to the bare
# "sportsbot/1.0" agent from a Linode address on 2026-10-13 (the same
# request from another network returned 200), so the client identifies
# itself the way a browser does and states what it accepts. Override with
# SPORTSBOT_HTTP_USER_AGENT if a host needs something else.
DEFAULT_USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 sportsbot/1.0")


def http_headers() -> dict[str, str]:
    return {"User-Agent": os.environ.get("SPORTSBOT_HTTP_USER_AGENT") or DEFAULT_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9"}


# The browser-like headers did not end the 406s from the Linode (2026-10-20: still 406 Not Acceptable from the
# server while the same request from another network returned 200 with any headers), so a 406 now walks a short list
# of alternatives and keeps the first that answers 200 for the rest of the client's life: the CDN's verdict depends
# on the request's headers or scheme, and which one a host gets is not knowable from here. SPORTSBOT_STATSAPI_BASE
# overrides the base URL outright.
FALLBACKS: tuple[tuple[str, dict[str, str] | None], ...] = (
    ("https://statsapi.mlb.com/api/v1", {"User-Agent": "curl/8.5.0", "Accept": "*/*"}),
    ("https://statsapi.mlb.com/api/v1", None),            # httpx's own default headers
    ("http://statsapi.mlb.com/api/v1", {"User-Agent": "curl/8.5.0", "Accept": "*/*"}),
)


class MLBStatsClient:
    def __init__(self, timeout: float = 30.0, transport: httpx.BaseTransport | None = None) -> None:
        self.timeout = timeout
        self.transport = transport
        self.base = os.environ.get("SPORTSBOT_STATSAPI_BASE") or BASE
        self.http = httpx.Client(timeout=timeout, headers=http_headers(), transport=transport)

    def _client(self, headers: dict[str, str] | None) -> httpx.Client:
        kw: dict = {"timeout": self.timeout, "transport": self.transport}
        if headers is not None:
            kw["headers"] = headers
        return httpx.Client(**kw)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=8), reraise=True)
    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self.http.get(f"{self.base}{path}", params=params)
        if resp.status_code == 406:
            for base, headers in FALLBACKS:
                client = self._client(headers)
                alt = client.get(f"{base}{path}", params=params)
                if alt.status_code == 200:
                    log.warning("MLB Stats API: 406 with the default request; %s with %s headers answers 200, using it",
                                base, "default httpx" if headers is None else headers.get("User-Agent"))
                    self.http, self.base = client, base
                    return alt.json()
                client.close()
        resp.raise_for_status()
        return resp.json()

    def _schedule(self, start: date, end: date, hydrate_sp: bool = True) -> list[dict]:
        games: list[dict] = []
        params = {
            "sportId": 1,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
        }
        if hydrate_sp:
            params["hydrate"] = "probablePitcher"
        data = self._get("/schedule", params=params)
        for d in data.get("dates", []):
            games.extend(d.get("games", []))
        return games

    @staticmethod
    def _game_dt(g: dict) -> datetime:
        raw = g.get("gameDate", "")
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)

    @staticmethod
    def _sp_name(team_side: dict) -> Optional[str]:
        sp = team_side.get("probablePitcher") or {}
        name = sp.get("fullName")
        return normalize_team(name) if name else None

    def results(self, start: date, end: date) -> list[GameResult]:
        """Final regular/post-season results in the window, date-ordered.
        Fetched in ~30-day chunks — the schedule endpoint misbehaves on
        multi-month ranges."""
        games: list[dict] = []
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(end, chunk_start + timedelta(days=29))
            games.extend(self._schedule(chunk_start, chunk_end))
            chunk_start = chunk_end + timedelta(days=1)
        out: list[GameResult] = []
        for g in games:
            status = (g.get("status") or {}).get("abstractGameState")
            if status != "Final":
                continue
            if g.get("gameType") not in ("R", "P", "F", "D", "L", "W"):
                continue
            teams = g.get("teams", {})
            home_t, away_t = teams.get("home", {}), teams.get("away", {})
            if "score" not in home_t or "score" not in away_t:
                continue
            out.append(
                GameResult(
                    date=self._game_dt(g),
                    home=normalize_team((home_t.get("team") or {}).get("name", "")),
                    away=normalize_team((away_t.get("team") or {}).get("name", "")),
                    home_score=int(home_t["score"]),
                    away_score=int(away_t["score"]),
                    home_sp=self._sp_name(home_t),
                    away_sp=self._sp_name(away_t),
                )
            )
        out.sort(key=lambda r: r.date)
        return out

    def upcoming(self, days: int = 2, back_days: int = 0) -> list[UpcomingGame]:
        """Scheduled (and live) games from today forward, with probable
        pitchers; ``back_days`` also reaches games filed under earlier dates
        (an evening game's date is the US date, a UTC day behind)."""
        today = datetime.now(timezone.utc).date()
        out: list[UpcomingGame] = []
        for g in self._schedule(today - timedelta(days=back_days),
                                today + timedelta(days=days)):
            status = (g.get("status") or {}).get("abstractGameState")
            if status not in ("Preview", "Live"):
                continue
            teams = g.get("teams", {})
            home_t, away_t = teams.get("home", {}), teams.get("away", {})
            out.append(
                UpcomingGame(
                    game_id=int(g.get("gamePk", 0)),
                    date=self._game_dt(g),
                    home=normalize_team((home_t.get("team") or {}).get("name", "")),
                    away=normalize_team((away_t.get("team") or {}).get("name", "")),
                    home_sp=self._sp_name(home_t),
                    away_sp=self._sp_name(away_t),
                    meta={"status": status, "game_type": g.get("gameType")},
                )
            )
        return out

    def history(self, seasons: int = 3) -> list[GameResult]:
        """Multi-season result history for Elo training."""
        end = datetime.now(timezone.utc).date()
        start = date(end.year - seasons, 1, 1)
        return self.results(start, end)
