"""NWS forecast signal for the Kalshi weather-dailies arm.

api.weather.gov is a free public API intended for programmatic use. We pull
the point forecast for each Kalshi settlement station, keep the daytime-high
periods, and convert a forecast high into P(market resolves YES) with a
normal error model around the point forecast (day-ahead NWS high-temperature
MAE is ~2°F; sigma defaults a little wider). Whole-degree settlement gets a
continuity correction: ">82" is TMAX >= 83, "<75" is TMAX <= 74.

Decision-time discipline: this module only computes; the snapshot service
records forecasts with a timestamp, and the exporter refuses any forecast
recorded after a market's first (max-lead) snapshot — forecasts are never
backfilled onto past decisions.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Optional

import httpx

from sportsbot.substrate_bridge.climatology import parse_market

log = logging.getLogger(__name__)

USER_AGENT = "sportsbot-substrate (research; repo: pointlessbassmusic-byte/pointless-repo)"
DEFAULT_SIGMA = 2.6   # °F std-dev around the point forecast for next-day highs

# Kalshi settlement stations (same mapping as climatology.py), lat/lon.
STATION_COORDS = {
    "KXHIGHNY": (40.7794, -73.9692),    # NYC Central Park
    "KXHIGHCHI": (41.9602, -87.9316),   # Chicago O'Hare
    "KXHIGHMIA": (25.7881, -80.3169),   # Miami Intl
    "KXHIGHAUS": (30.3208, -97.7604),   # Austin Camp Mabry
    "KXHIGHDEN": (39.8467, -104.6561),  # Denver Intl
    "KXHIGHLAX": (33.9382, -118.3865),  # Los Angeles Intl
    "KXHIGHPHIL": (39.8683, -75.2311),  # Philadelphia Intl
}

_forecast_urls: dict[str, str] = {}   # series -> gridpoint forecast URL (static)


def _get(url: str, timeout: float = 30.0) -> dict:
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT,
                                   "Accept": "application/geo+json"}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def forecast_highs(series: str) -> dict[date, int]:
    """{target_date: forecast_high_F} for the series' station (~7 days out).
    Only daytime periods count — a daily-high market settles on the daytime max."""
    coords = STATION_COORDS.get(series)
    if coords is None:
        return {}
    if series not in _forecast_urls:
        pt = _get(f"https://api.weather.gov/points/{coords[0]},{coords[1]}")
        _forecast_urls[series] = pt["properties"]["forecast"]
    data = _get(_forecast_urls[series])
    out: dict[date, int] = {}
    for period in data.get("properties", {}).get("periods", []):
        if not period.get("isDaytime"):
            continue
        try:
            d = datetime.fromisoformat(period["startTime"]).date()
            out[d] = int(round(float(period["temperature"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def sigma_for_lead(lead_days: float) -> float:
    """Forecast-error std-dev grows with lead time (NWS verification: high-temp
    MAE ~1.5-2°F day-1 rising toward ~4-5°F day-7). Linear ramp, clamped."""
    return min(5.5, max(1.8, 1.8 + 0.55 * max(0.0, lead_days)))


def prob_from_high(title: str, forecast_high: float,
                   sigma: float = DEFAULT_SIGMA) -> Optional[float]:
    """P(YES) for a Kalshi daily-high market title given a forecast high."""
    parsed = parse_market(title)
    if parsed is None or sigma <= 0:
        return None
    _target, op, lo, hi = parsed
    if op == ">":                       # TMAX >= lo + 1
        p = 1.0 - _phi((lo + 0.5 - forecast_high) / sigma)
    elif op == "<":                     # TMAX <= lo - 1
        p = _phi((lo - 0.5 - forecast_high) / sigma)
    else:                               # lo <= TMAX <= hi
        p = _phi((hi + 0.5 - forecast_high) / sigma) - \
            _phi((lo - 0.5 - forecast_high) / sigma)
    return min(0.99, max(0.01, p))
