"""Tennis historical results from Jeff Sackmann's open datasets
(github.com/JeffSackmann/tennis_atp and tennis_wta, CC BY-NC-SA).

CSV schema (per match): tourney_id, tourney_name, surface, draw_size,
tourney_level, tourney_date (YYYYMMDD), match_num, winner_id, winner_name,
loser_id, loser_name, score, best_of, round, minutes, w_ace..l_bpFaced.

We download per-year files, cache locally, and emit normalized MatchResult
rows ordered by date for Elo training.
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Optional

import httpx

log = logging.getLogger(__name__)

RAW_BASE = "https://raw.githubusercontent.com/JeffSackmann/{repo}/master/{repo}_matches_{year}.csv"

SURFACES = ("Hard", "Clay", "Grass", "Carpet")


@dataclass
class MatchResult:
    date: datetime
    winner: str
    loser: str
    surface: str            # Hard | Clay | Grass | Carpet | ""
    best_of: int
    level: str              # G, M, A, C, F ... (tour level)
    tourney: str
    games_share: float = 0.58   # winner's share of games (WElo margin signal)
    retirement: bool = False    # match ended by retirement (halve rating update)


def parse_score(score: str) -> tuple[float, bool]:
    """Return (winner's share of games, retirement flag) from a score string
    like '6-4 3-6 7-6(5)' or '6-2 3-1 RET'. Falls back to the average winner
    share (0.58) when unparseable.
    """
    retired = "RET" in score.upper()
    w_games = l_games = 0
    for token in score.split():
        token = token.split("(")[0]  # drop tiebreak detail
        parts = token.split("-")
        if len(parts) != 2:
            continue
        try:
            a, b = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if a > 20 or b > 20:  # match-tiebreak scores like 10-8 are "one game"
            a, b = (1, 0) if a > b else (0, 1)
        w_games += a
        l_games += b
    total = w_games + l_games
    share = w_games / total if total > 0 else 0.58
    return share, retired


def normalize_player(name: str) -> str:
    """Canonical player key: lowercase, collapsed whitespace."""
    return " ".join(name.strip().lower().split())


def download_year(tour: str, year: int, cache_dir: str = "data/raw/tennis",
                  force: bool = False, timeout: float = 60.0) -> Optional[str]:
    """Fetch one year of one tour ('atp'|'wta'); returns local path or None."""
    repo = f"tennis_{tour}"
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{repo}_matches_{year}.csv")
    if os.path.exists(path) and not force:
        return path
    url = RAW_BASE.format(repo=repo, year=year)
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        if resp.status_code == 404:
            log.info("no data for %s %s", tour, year)
            return None
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("tennis download failed %s %s: %s", tour, year, exc)
        return None
    with open(path, "wb") as fh:
        fh.write(resp.content)
    return path


def load_matches(paths: list[str]) -> list[MatchResult]:
    """Parse cached CSVs into date-ordered MatchResults (skips retirements
    recorded as W/O with no play is NOT possible from the score field alone;
    walkovers 'W/O' are dropped)."""
    out: list[MatchResult] = []
    for path in paths:
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh):
                score = (row.get("score") or "").strip().upper()
                if "W/O" in score or "WEA" in score or not row.get("winner_name"):
                    continue
                raw_date = (row.get("tourney_date") or "").strip()
                try:
                    date = datetime.strptime(raw_date, "%Y%m%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                try:
                    best_of = int(row.get("best_of") or 3)
                except ValueError:
                    best_of = 3
                games_share, retired = parse_score(score)
                out.append(
                    MatchResult(
                        date=date,
                        winner=normalize_player(row["winner_name"]),
                        loser=normalize_player(row.get("loser_name", "")),
                        surface=(row.get("surface") or "").strip() or "Hard",
                        best_of=best_of,
                        level=(row.get("tourney_level") or "").strip(),
                        tourney=(row.get("tourney_name") or "").strip(),
                        games_share=games_share,
                        retirement=retired,
                    )
                )
    out.sort(key=lambda m: m.date)
    return out


def fetch_history(tours: tuple[str, ...] = ("atp", "wta"),
                  start_year: int = 2015,
                  end_year: Optional[int] = None,
                  cache_dir: str = "data/raw/tennis") -> list[MatchResult]:
    """Download + parse all requested years. end_year defaults to now."""
    end_year = end_year or datetime.now(timezone.utc).year
    paths = []
    for tour in tours:
        for year in range(start_year, end_year + 1):
            p = download_year(tour, year, cache_dir=cache_dir)
            if p:
                paths.append(p)
    return load_matches(paths)


def iter_by_tour(matches: list[MatchResult], tour_levels: str = "") -> Iterator[MatchResult]:
    for m in matches:
        if not tour_levels or m.level in tour_levels:
            yield m
