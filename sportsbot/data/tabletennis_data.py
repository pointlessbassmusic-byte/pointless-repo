"""Table tennis results ingestion.

Reality check: unlike tennis (Sackmann) and MLB (Stats API) there is no
authoritative free historical feed for the fast leagues that dominate
table-tennis betting volume (Setka Cup, TT Cup, Czech Liga Pro) — those
require a paid feed (BetsAPI, api-sports) or a scraper you maintain.

This module therefore ingests a *generic CSV* you can populate from any
source, plus a small helper to bootstrap ratings from Polymarket's own
resolved markets (free, and exactly the population we trade):

CSV schema: date (ISO), winner, loser, league, best_of
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)


@dataclass
class TTMatchResult:
    date: datetime
    winner: str
    loser: str
    league: str = ""
    best_of: int = 5


def normalize_player(name: str) -> str:
    return " ".join(name.strip().lower().split())


def load_csv(path: str) -> list[TTMatchResult]:
    out: list[TTMatchResult] = []
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            if not row.get("winner") or not row.get("loser"):
                continue
            try:
                dt = datetime.fromisoformat(row["date"].strip())
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            try:
                best_of = int(row.get("best_of") or 5)
            except ValueError:
                best_of = 5
            out.append(
                TTMatchResult(
                    date=dt,
                    winner=normalize_player(row["winner"]),
                    loser=normalize_player(row["loser"]),
                    league=(row.get("league") or "").strip(),
                    best_of=best_of,
                )
            )
    out.sort(key=lambda m: m.date)
    return out


def results_from_polymarket(days_back: int = 60, timeout: float = 30.0) -> list[TTMatchResult]:
    """Bootstrap ratings from Polymarket's resolved table-tennis moneylines.

    Free and perfectly matched to the traded population; thin history per
    player, so treat the resulting ratings as high-uncertainty.
    """
    import httpx

    from sportsbot.exchanges.polymarket import GAMMA_BASE, SPORT_TAGS, _parse_json_field

    out: list[TTMatchResult] = []
    offset = 0
    with httpx.Client(timeout=timeout) as client:
        while True:
            resp = client.get(
                f"{GAMMA_BASE}/events",
                params={
                    "tag_id": SPORT_TAGS["table_tennis"],
                    "closed": "true",
                    "order": "endDate",
                    "ascending": "false",
                    "limit": 100,
                    "offset": offset,
                },
            )
            resp.raise_for_status()
            events = resp.json()
            if not events:
                break
            for ev in events:
                for m in ev.get("markets") or []:
                    if m.get("sportsMarketType") != "moneyline":
                        continue
                    outcomes = _parse_json_field(m.get("outcomes"))
                    prices = _parse_json_field(m.get("outcomePrices"))
                    if len(outcomes) != 2 or len(prices) != 2:
                        continue
                    try:
                        p0 = float(prices[0])
                    except (TypeError, ValueError):
                        continue
                    if p0 not in (0.0, 1.0):  # unresolved
                        continue
                    winner_idx = 0 if p0 == 1.0 else 1
                    raw_end = m.get("endDate") or ev.get("endDate") or ""
                    try:
                        dt = datetime.fromisoformat(str(raw_end).replace("Z", "+00:00"))
                    except ValueError:
                        dt = datetime.now(timezone.utc)
                    out.append(
                        TTMatchResult(
                            date=dt,
                            winner=normalize_player(str(outcomes[winner_idx])),
                            loser=normalize_player(str(outcomes[1 - winner_idx])),
                            league=(ev.get("slug") or "").rsplit("-", 3)[0],
                        )
                    )
            offset += 100
            if offset >= 2000:
                break
    cutoff_days = days_back
    now = datetime.now(timezone.utc)
    out = [r for r in out if (now - r.date).days <= cutoff_days]
    out.sort(key=lambda m: m.date)
    return out
