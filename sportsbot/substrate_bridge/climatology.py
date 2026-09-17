"""Climatology baseline for the Kalshi weather-dailies substrate arm.

Turns the ingest baseline_prob from the 0.5 no-skill stand-in into a real
conventional model: the empirical distribution of the settlement station's
daily maximum temperature (GHCND TMAX via NOAA NCEI, whole degrees F) in a
±7-day day-of-year window across prior years.

No-leak rule (substrate protocol: baselines are decision-time, never
post-decision): only years STRICTLY BEFORE the market's target year enter the
window — the target year is excluded entirely, so no observation on or after
the decision time can contribute, even for markets exported long after they
settled.

Station mapping is the NWS climate-report station Kalshi settles against
(best-effort; verify against each series' rulebook before leaning on a city):
Central Park (NY), O'Hare (CHI), Miami Intl (MIA), Camp Mabry (AUS),
Denver Intl (DEN), LAX, Philadelphia Intl (PHIL).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime
from typing import Optional

import httpx

log = logging.getLogger(__name__)

NCEI_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
CACHE_DIR = "data/cache"
HISTORY_START = "1990-01-01"
WINDOW_DAYS = 7

STATIONS = {
    "KXHIGHNY": "USW00094728",    # NYC Central Park
    "KXHIGHCHI": "USW00094846",   # Chicago O'Hare
    "KXHIGHMIA": "USW00012839",   # Miami Intl
    "KXHIGHAUS": "USW00013958",   # Austin Camp Mabry
    "KXHIGHDEN": "USW00003017",   # Denver Intl
    "KXHIGHLAX": "USW00023174",   # Los Angeles Intl
    "KXHIGHPHIL": "USW00013739",  # Philadelphia Intl
}

# "Will the maximum temperature be >82° on Sep 16, 2026?"
# "... be <75° on ..."   "... be 81-82° on ..."
_TITLE_RE = re.compile(
    r"be\s*(?P<op>[<>])?\s*(?P<lo>\d+)(?:-(?P<hi>\d+))?°\s*on\s*"
    r"(?P<date>[A-Za-z]+ \d{1,2}, \d{4})")


def parse_market(title: str) -> Optional[tuple[date, str, int, int]]:
    """-> (target_date, op, lo, hi) where op is '>', '<' or 'between'.
    Temperatures settle in whole °F: '>82' means TMAX >= 83; '<75' means
    TMAX <= 74; '81-82' means 81 <= TMAX <= 82."""
    m = _TITLE_RE.search(title or "")
    if not m:
        return None
    try:
        target = datetime.strptime(m.group("date"), "%b %d, %Y").date()
    except ValueError:
        return None
    lo = int(m.group("lo"))
    if m.group("hi") is not None:
        return target, "between", lo, int(m.group("hi"))
    op = m.group("op")
    if op not in (">", "<"):
        return None
    return target, op, lo, lo


def _satisfies(tmax: int, op: str, lo: int, hi: int) -> bool:
    if op == ">":
        return tmax > lo
    if op == "<":
        return tmax < lo
    return lo <= tmax <= hi


class Climatology:
    """Loads (and caches) station TMAX history; answers market probabilities."""

    def __init__(self, cache_dir: str = CACHE_DIR):
        self.cache_dir = cache_dir
        self._records: dict[str, dict[str, int]] = {}   # station -> {iso date: tmax}

    # -- data --------------------------------------------------------------
    def _cache_path(self, station: str) -> str:
        return os.path.join(self.cache_dir, f"climo_{station}.json")

    def load_station(self, station: str) -> dict[str, int]:
        if station in self._records:
            return self._records[station]
        path = self._cache_path(station)
        if os.path.exists(path):
            with open(path) as fh:
                self._records[station] = {k: int(v) for k, v in json.load(fh).items()}
            return self._records[station]
        log.info("climatology: downloading TMAX history for %s", station)
        resp = httpx.get(NCEI_URL, params={
            "dataset": "daily-summaries", "stations": station,
            "startDate": HISTORY_START, "endDate": date.today().isoformat(),
            "dataTypes": "TMAX", "units": "standard", "format": "json",
        }, timeout=120)
        resp.raise_for_status()
        records: dict[str, int] = {}
        for row in resp.json():
            try:
                records[row["DATE"]] = int(round(float(row["TMAX"])))
            except (KeyError, TypeError, ValueError):
                continue
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(path, "w") as fh:
            json.dump(records, fh)
        self._records[station] = records
        return records

    def set_station_records(self, station: str, records: dict[str, int]) -> None:
        """Inject records directly (tests / offline use)."""
        self._records[station] = dict(records)

    # -- probability -------------------------------------------------------
    def prob(self, series: str, title: str) -> Optional[float]:
        """Laplace-smoothed empirical P(market resolves YES) from prior years'
        TMAX in a ±WINDOW_DAYS day-of-year window. None when unanswerable."""
        station = STATIONS.get(series)
        parsed = parse_market(title)
        if station is None or parsed is None:
            return None
        target, op, lo, hi = parsed
        try:
            records = self.load_station(station)
        except Exception:  # noqa: BLE001 — network/cache failure = no baseline, never a crash
            log.exception("climatology load failed for %s", station)
            return None

        target_doy = target.timetuple().tm_yday
        hits = n = 0
        for iso, tmax in records.items():
            d = date.fromisoformat(iso)
            if d.year >= target.year:        # no-leak rule: prior years only
                continue
            delta = abs(d.timetuple().tm_yday - target_doy)
            if min(delta, 365 - delta) > WINDOW_DAYS:
                continue
            n += 1
            hits += _satisfies(tmax, op, lo, hi)
        if n < 60:                            # ~4+ usable years before we trust it
            return None
        return (hits + 1) / (n + 2)
