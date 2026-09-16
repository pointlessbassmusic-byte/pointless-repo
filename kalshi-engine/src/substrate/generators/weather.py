"""Weather generator: forecast-based probabilities for daily high-temp markets.

Kalshi's KXHIGH* series settle on a city's daily maximum temperature. A free
open-meteo forecast gives the expected high; modeling the forecast error as
Normal(forecast, sigma) with sigma growing by lead time turns each strike band
into a probability:

    "70° to 71°"  (floor=70, cap=71)  ->  P(69.5 < T < 71.5)
    "69° or below" (cap=70)           ->  P(T < 69.5)
    "81° or above" (floor=80)         ->  P(T > 80.5)

One forecast fetch per station per cache TTL covers every market and date.
"""
from __future__ import annotations

import logging
import math
import re
import time
from datetime import date, datetime, timezone

from ..base import Context, Forecast, SignalGenerator
from ...client import Market
from ...http_util import retrying_session

log = logging.getLogger(__name__)

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

# month abbreviation in Kalshi event tickers (KXHIGHNY-26SEP16)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
_DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(?:-|$)")

# default station coordinates for Kalshi's daily-high series (approximate
# observation sites; sigma absorbs small siting differences)
DEFAULT_STATIONS = {
    "KXHIGHNY": {"latitude": 40.783, "longitude": -73.967, "timezone": "America/New_York"},
    "KXHIGHCHI": {"latitude": 41.786, "longitude": -87.752, "timezone": "America/Chicago"},
    "KXHIGHMIA": {"latitude": 25.788, "longitude": -80.317, "timezone": "America/New_York"},
    "KXHIGHAUS": {"latitude": 30.183, "longitude": -97.680, "timezone": "America/Chicago"},
    "KXHIGHDEN": {"latitude": 39.847, "longitude": -104.656, "timezone": "America/Denver"},
    "KXHIGHLAX": {"latitude": 33.938, "longitude": -118.389, "timezone": "America/Los_Angeles"},
    "KXHIGHPHIL": {"latitude": 39.873, "longitude": -75.227, "timezone": "America/New_York"},
}


def _event_date(ticker: str) -> date | None:
    m = _DATE_RE.search(ticker)
    if not m:
        return None
    yy, mon, dd = m.groups()
    month = _MONTHS.get(mon)
    if month is None:
        return None
    try:
        return date(2000 + int(yy), month, int(dd))
    except ValueError:
        return None


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def band_probability(mu: float, sigma: float,
                     floor: float | None, cap: float | None) -> float | None:
    """P(daily high lands in this strike band), with a half-degree continuity
    correction because settlement temperatures are whole degrees."""
    if floor is not None and cap is not None:
        return _normal_cdf(cap + 0.5, mu, sigma) - _normal_cdf(floor - 0.5, mu, sigma)
    if cap is not None:      # "cap-1 or below" => T < cap
        return _normal_cdf(cap - 0.5, mu, sigma)
    if floor is not None:    # "floor+1 or above" => T > floor
        return 1 - _normal_cdf(floor + 0.5, mu, sigma)
    return None


class WeatherHigh(SignalGenerator):
    name = "weather"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.stations = cfg.get("stations") or DEFAULT_STATIONS
        self.sigma_base = float(cfg.get("sigma_base_f", 1.8))
        self.sigma_per_day = float(cfg.get("sigma_per_day_f", 0.6))
        self.cache_ttl = float(cfg.get("cache_ttl_sec", 1800))
        self.http = retrying_session()
        # series prefix -> (fetched_monotonic, {iso_date: forecast_high_f})
        self._cache: dict[str, tuple[float, dict[str, float]]] = {}

    def _forecasts(self, prefix: str, station: dict) -> dict[str, float]:
        cached = self._cache.get(prefix)
        if cached and time.monotonic() - cached[0] < self.cache_ttl:
            return cached[1]
        out: dict[str, float] = {}
        try:
            r = self.http.get(OPEN_METEO, params={
                "latitude": station["latitude"], "longitude": station["longitude"],
                "daily": "temperature_2m_max", "temperature_unit": "fahrenheit",
                "forecast_days": 16, "timezone": station.get("timezone", "UTC"),
            }, timeout=20)
            r.raise_for_status()
            daily = r.json().get("daily", {})
            out = {d: t for d, t in zip(daily.get("time", []),
                                        daily.get("temperature_2m_max", []))
                   if t is not None}
        except Exception:  # noqa: BLE001 — a dead weather feed must not sink the cycle
            log.warning("weather forecast fetch failed for %s", prefix, exc_info=True)
        # cache failures too (briefly, via the same TTL) so one outage doesn't
        # retry per-market within a cycle
        self._cache[prefix] = (time.monotonic(), out)
        return out

    def forecast(self, market: Market, ctx: Context) -> Forecast | None:
        prefix = market.ticker.split("-", 1)[0]
        station = self.stations.get(prefix)
        if station is None:
            return None
        target = _event_date(market.event_ticker or market.ticker)
        if target is None:
            return None
        mu = self._forecasts(prefix, station).get(target.isoformat())
        if mu is None:
            return None
        days_ahead = max(0, (target - datetime.now(timezone.utc).date()).days)
        sigma = self.sigma_base + self.sigma_per_day * days_ahead
        p = band_probability(mu, sigma, market.floor_strike, market.cap_strike)
        if p is None:
            return None
        return Forecast(
            generator=self.name,
            prob_yes=p,
            confidence=self.confidence,
            rationale=(f"forecast high {mu:.1f}F ±{sigma:.1f} for {target}; "
                       f"band [{market.floor_strike},{market.cap_strike}] -> {p:.2f}"),
        )
