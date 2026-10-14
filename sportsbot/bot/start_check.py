"""Cross-check a venue's MLB start time against MLB's own schedule.

Kalshi's MLB ticker embeds the first pitch as scheduled when the market
was LISTED, and a game moved afterwards keeps the old time: on 120
settled KXMLBGAME games (2026-09-22 .. 10-12) six tickers ran 0.6 to 3.5
hours LATE (rescheduled doubleheader games, a postseason game set at
17:00 ET whose ticker says 20:00). With the ticker as the clock the risk
layer's pre-start cutoff and the touch arm's quoting window point hours
into a live game, which is where a resting quote is picked off
(EDGE_VERDICT Result 22).

Rule: a baseball market's start becomes the EARLIER of the venue's time
and the matched game's scheduled first pitch (a game already Live counts
as started now at the latest). Tighten-only: a start is never moved
later, a market the schedule cannot match keeps the venue's time, and a
venue without a start stays without one (the risk layer refuses it).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Iterable

from sportsbot.bot.matching import similarity
from sportsbot.core.types import MarketInfo, Sport

log = logging.getLogger(__name__)

MATCH_MIN = 0.85
MATCH_WINDOW = timedelta(hours=12)


def _same_game(m: MarketInfo, home: str, away: str) -> bool:
    a, b = m.home or "", m.away or ""
    direct = similarity(a, home) > MATCH_MIN and similarity(b, away) > MATCH_MIN
    swapped = similarity(a, away) > MATCH_MIN and similarity(b, home) > MATCH_MIN
    return direct or swapped


def earliest_starts(markets: Iterable[MarketInfo], games, now: datetime
                    ) -> tuple[list[MarketInfo], int]:
    """(markets with baseball starts pulled earlier where the schedule says
    so, how many moved). ``games`` are `mlb_data.UpcomingGame`s."""
    out, moved = [], 0
    games = list(games)
    for m in markets:
        if m.sport is not Sport.BASEBALL or m.start_time is None or not m.home:
            out.append(m)
            continue
        cands = [g for g in games if _same_game(m, g.home, g.away)
                 and abs(g.date - m.start_time) <= MATCH_WINDOW]
        if not cands:
            out.append(m)
            continue
        g = min(cands, key=lambda g: abs(g.date - m.start_time))
        sched = g.date
        if (g.meta or {}).get("status") == "Live":
            sched = min(sched, now)
        if sched < m.start_time:
            log.warning("start for %s moved %s -> %s (MLB schedule%s)", m.market_id,
                        m.start_time.isoformat(), sched.isoformat(),
                        ", game live" if (g.meta or {}).get("status") == "Live" else "")
            m = m.model_copy(update={"start_time": sched})
            moved += 1
        out.append(m)
    return out, moved
