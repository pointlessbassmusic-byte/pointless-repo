"""Weather generator: forecast-based probabilities for daily high-temp markets.

Kalshi's KXHIGH* series settle on a city's daily maximum temperature. A free
open-meteo forecast gives the expected high; modeling the forecast error as
Normal(forecast, sigma) with sigma growing by lead time turns each strike band
into a probability:

    "70° to 71°"  (floor=70, cap=71)  ->  P(69.5 < T < 71.5)
    "69° or below" (cap=70)           ->  P(T < 69.5)
    "81° or above" (floor=80)         ->  P(T > 80.5)

One forecast fetch per station per cache TTL covers every market and date.

The generator stands down once a day's extremum window has closed in
station-local time: the low is set overnight and the high by late afternoon,
so from then on the market knows the realized value and we only hold a
forecast. Lead time is measured in station-local days for the same reason.
"""
from __future__ import annotations

import logging
import math
import re
import time
from datetime import date, datetime, timedelta, timezone

from ..base import Context, Forecast, SignalGenerator
from ...client import Market
from ...http_util import retrying_session

log = logging.getLogger(__name__)

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

# month abbreviation in Kalshi event tickers (KXHIGHNY-26SEP16)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
_DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(?:-|$)")

# default station coordinates for Kalshi's daily temperature series
# (approximate observation sites; sigma absorbs small siting differences).
# "variable": "min" marks daily-LOW series; default is the daily high.
# All entries verified to have open markets with Fahrenheit strikes
# (probe 2026-09-17); international series existed but listed no open
# markets at the time and were left out.
_C = {
    "nyc": (40.783, -73.967, "America/New_York"),
    "chi": (41.786, -87.752, "America/Chicago"),
    "mia": (25.788, -80.317, "America/New_York"),
    "aus": (30.183, -97.680, "America/Chicago"),
    "den": (39.847, -104.656, "America/Denver"),
    "lax": (33.938, -118.389, "America/Los_Angeles"),
    "phl": (39.873, -75.227, "America/New_York"),
    "bos": (42.361, -71.010, "America/New_York"),
    "dc": (38.847, -77.038, "America/New_York"),
    "dal": (32.847, -96.852, "America/Chicago"),
    "sea": (47.445, -122.314, "America/Los_Angeles"),
    "sfo": (37.620, -122.365, "America/Los_Angeles"),
    "phx": (33.428, -112.004, "America/Phoenix"),
    "lv": (36.072, -115.163, "America/Los_Angeles"),
    "atl": (33.630, -84.442, "America/New_York"),
    "nola": (29.993, -90.251, "America/Chicago"),
    "okc": (35.389, -97.601, "America/Chicago"),
    "satx": (29.534, -98.470, "America/Chicago"),
    "ewr": (40.693, -74.169, "America/New_York"),
}


def _st(city: str, variable: str = "max") -> dict:
    lat, lon, tz = _C[city]
    return {"latitude": lat, "longitude": lon, "timezone": tz, "variable": variable}


DEFAULT_STATIONS = {
    # daily highs
    "KXHIGHNY": _st("nyc"), "KXHIGHCHI": _st("chi"), "KXHIGHMIA": _st("mia"),
    "KXHIGHAUS": _st("aus"), "KXHIGHDEN": _st("den"), "KXHIGHLAX": _st("lax"),
    "KXHIGHPHIL": _st("phl"), "KXHIGHTBOS": _st("bos"), "KXHIGHTDC": _st("dc"),
    "KXHIGHTDAL": _st("dal"), "KXHIGHTSEA": _st("sea"), "KXHIGHTSFO": _st("sfo"),
    "KXHIGHTPHX": _st("phx"), "KXHIGHTLV": _st("lv"), "KXHIGHTATL": _st("atl"),
    "KXHIGHTNOLA": _st("nola"), "KXHIGHTEWR": _st("ewr"),
    # daily lows
    "KXLOWTBOS": _st("bos", "min"), "KXLOWTDC": _st("dc", "min"),
    "KXLOWTDAL": _st("dal", "min"), "KXLOWTSEA": _st("sea", "min"),
    "KXLOWTSFO": _st("sfo", "min"), "KXLOWTPHX": _st("phx", "min"),
    "KXLOWTOKC": _st("okc", "min"), "KXLOWTSATX": _st("satx", "min"),
}


def _daily_variable(station: dict) -> str:
    return "temperature_2m_min" if station.get("variable") == "min" else "temperature_2m_max"


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
        # station-local hour after which the day's extremum is effectively
        # realized and the market's price beats our forecast
        self.realized_hour_min = int(cfg.get("realized_hour_min", 10))
        self.realized_hour_max = int(cfg.get("realized_hour_max", 17))
        self.http = retrying_session()
        # prefix -> (fetched_monotonic, {iso_date: forecast_f}, utc_offset_sec)
        self._cache: dict[str, tuple[float, dict[str, float], int]] = {}

    def _forecasts(self, prefix: str, station: dict) -> dict[str, float]:
        cached = self._cache.get(prefix)
        if cached and time.monotonic() - cached[0] < self.cache_ttl:
            return cached[1]
        # batch-fetch every station sharing this variable in ONE request —
        # per-station requests (25+/cycle, times retries) trip open-meteo's
        # rate limit and starve the whole cycle
        var = _daily_variable(station)
        group = [(name, st) for name, st in self.stations.items()
                 if _daily_variable(st) == var]
        now = time.monotonic()
        try:
            r = self.http.get(OPEN_METEO, params={
                "latitude": ",".join(str(st["latitude"]) for _, st in group),
                "longitude": ",".join(str(st["longitude"]) for _, st in group),
                "timezone": ",".join(st.get("timezone", "UTC") for _, st in group),
                "daily": var, "temperature_unit": "fahrenheit",
                "forecast_days": 16,
            }, timeout=30)
            r.raise_for_status()
            payload = r.json()
            results = payload if isinstance(payload, list) else [payload]
            for (name, _), loc in zip(group, results):
                daily = loc.get("daily", {})
                self._cache[name] = (now, {
                    d: t for d, t in zip(daily.get("time", []), daily.get(var, []))
                    if t is not None}, int(loc.get("utc_offset_seconds") or 0))
        except Exception:  # noqa: BLE001 — a dead weather feed must not sink the cycle
            log.warning("weather batch fetch failed (%s)", var, exc_info=True)
            for name, _ in group:  # cache the failure briefly: no per-market retries
                self._cache.setdefault(name, (now, {}, 0))
        return self._cache.get(prefix, (now, {}, 0))[1]

    def _local_now(self, prefix: str) -> datetime:
        """Station-local wall clock, from open-meteo's utc_offset_seconds (DST
        correct, no tzdata needed). Both the lead time and the realized-window
        check need local dates: at 01:00 UTC a US station is still on yesterday,
        which otherwise reads a same-day market as a past-day one."""
        cached = self._cache.get(prefix)
        return datetime.now(timezone.utc) + timedelta(seconds=cached[2] if cached else 0)

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
        # per-station bias correction: bias_f = mean(observed - forecast), fitted
        # by weather_calibrate from Kalshi-settled truth. Grid-cell forecasts can
        # run systematically hot/cold vs the exact settlement station.
        mu += float(station.get("bias_f", 0.0))
        # A day whose extremum has already been realized belongs to the market:
        # it sees the observed value, we only have a forecast. (Seen live: a
        # KXLOWTSATX day the book priced 0.89 YES scored 0.05 on this arm, and
        # the engine sank its largest stake of the cycle into the NO side.)
        local_now = self._local_now(prefix)
        lead_days = (target - local_now.date()).days
        if lead_days < 0:
            return None
        cutoff = (self.realized_hour_min if station.get("variable") == "min"
                  else self.realized_hour_max)
        if lead_days == 0 and local_now.hour >= cutoff:
            return None
        sigma = self.sigma_base + self.sigma_per_day * lead_days
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
