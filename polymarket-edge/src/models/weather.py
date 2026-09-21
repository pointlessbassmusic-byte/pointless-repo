"""Weather fair value for Polymarket daily-temperature markets.

Prices "Highest temperature in <city> ..." markets from a free open-meteo
forecast, modeling the daily high as Normal(forecast, sigma) with sigma
growing by lead time. Two question dialects:

    US cities (Fahrenheit bands):
        "... be 71°F or below on September 17?"
        "... be between 72-73°F on September 17?"
        "... be 84°F or above on September 17?"
    International cities (Celsius, single degrees):
        "... be 20°C or below on September 17?"
        "... be 21°C on September 17?"
        "... be 30°C or above on September 17?"

Sigma is configured in Fahrenheit and scaled by 5/9 for Celsius markets.
Mirrors kalshi-engine's weather generator math (kept in sync by hand, like
http_util) — the settlement/resolution loop and calibration report already
score these estimates against real outcomes.
"""
from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

from ..clients.gamma import GammaClient, SportsMarket
from ..http_util import retrying_session
from .fair_value import FairEstimate

log = logging.getLogger(__name__)

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

# city keyword -> (latitude, longitude, timezone, unit)
CITIES = {
    "new york": (40.783, -73.967, "America/New_York", "fahrenheit"),
    "los angeles": (33.938, -118.389, "America/Los_Angeles", "fahrenheit"),
    "chicago": (41.786, -87.752, "America/Chicago", "fahrenheit"),
    "miami": (25.788, -80.317, "America/New_York", "fahrenheit"),
    "austin": (30.183, -97.680, "America/Chicago", "fahrenheit"),
    "denver": (39.847, -104.656, "America/Denver", "fahrenheit"),
    "philadelphia": (39.873, -75.227, "America/New_York", "fahrenheit"),
    "houston": (29.646, -95.279, "America/Chicago", "fahrenheit"),
    "dallas": (32.847, -96.852, "America/Chicago", "fahrenheit"),
    "seattle": (47.445, -122.314, "America/Los_Angeles", "fahrenheit"),
    "london": (51.478, -0.461, "Europe/London", "celsius"),
    "paris": (49.010, 2.548, "Europe/Paris", "celsius"),
    "amsterdam": (52.310, 4.768, "Europe/Amsterdam", "celsius"),
    "munich": (48.354, 11.786, "Europe/Berlin", "celsius"),
    "milan": (45.630, 8.728, "Europe/Rome", "celsius"),
    "moscow": (55.973, 37.413, "Europe/Moscow", "celsius"),
    "cape town": (-33.971, 18.602, "Africa/Johannesburg", "celsius"),
    "beijing": (40.080, 116.585, "Asia/Shanghai", "celsius"),
    "shanghai": (31.144, 121.808, "Asia/Shanghai", "celsius"),
    "guangzhou": (23.392, 113.299, "Asia/Shanghai", "celsius"),
    "shenzhen": (22.639, 113.811, "Asia/Shanghai", "celsius"),
    "wuhan": (30.784, 114.208, "Asia/Shanghai", "celsius"),
    "chengdu": (30.578, 103.947, "Asia/Shanghai", "celsius"),
    "chongqing": (29.719, 106.642, "Asia/Shanghai", "celsius"),
    "hong kong": (22.309, 113.915, "Asia/Hong_Kong", "celsius"),
    "tokyo": (35.549, 139.780, "Asia/Tokyo", "celsius"),
    "seoul": (37.469, 126.451, "Asia/Seoul", "celsius"),
    "singapore": (1.359, 103.989, "Asia/Singapore", "celsius"),
    "sydney": (-33.946, 151.177, "Australia/Sydney", "celsius"),
    "toronto": (43.677, -79.625, "America/Toronto", "celsius"),
    "mexico city": (19.436, -99.072, "America/Mexico_City", "celsius"),
    "sao paulo": (-23.435, -46.473, "America/Sao_Paulo", "celsius"),
    "são paulo": (-23.435, -46.473, "America/Sao_Paulo", "celsius"),
}

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}

_Q_RE = re.compile(
    r"temperature in (?P<city>.+?) be "
    r"(?:between (?P<lo>-?\d+)\s*-\s*(?P<hi>-?\d+)|(?P<val>-?\d+))"
    r"\s*°(?P<unit>[CF])(?P<dir> or below| or above| or higher| or lower)?"
    r".*?on (?P<month>[A-Za-z]+) (?P<day>\d{1,2})",
    re.IGNORECASE,
)


@dataclass
class ParsedQuestion:
    city: str
    target: date
    floor: float | None   # P(T > floor) side
    cap: float | None     # P(T < cap) side; both set = inclusive band
    unit: str             # "fahrenheit" | "celsius"


def parse_question(question: str, year: int) -> ParsedQuestion | None:
    m = _Q_RE.search(question)
    if not m:
        return None
    city = m.group("city").strip().lower()
    if city.startswith("the "):
        city = city[4:]
    month = _MONTHS.get(m.group("month").lower())
    if month is None:
        return None
    try:
        target = date(year, month, int(m.group("day")))
    except ValueError:
        return None
    unit = "fahrenheit" if m.group("unit").upper() == "F" else "celsius"

    direction = (m.group("dir") or "").strip().lower()
    if m.group("lo") is not None:                      # inclusive band A-B
        floor, cap = float(m.group("lo")), float(m.group("hi"))
    else:
        v = float(m.group("val"))
        if direction in ("or below", "or lower"):      # T <= v
            floor, cap = None, v + 1
        elif direction in ("or above", "or higher"):   # T >= v
            floor, cap = v - 1, None
        else:                                          # exactly v (whole degrees)
            floor, cap = v, v
    return ParsedQuestion(city=city, target=target, floor=floor, cap=cap, unit=unit)


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def band_probability(mu: float, sigma: float,
                     floor: float | None, cap: float | None) -> float | None:
    """Same convention and continuity correction as kalshi-engine's generator."""
    if floor is not None and cap is not None:
        return _normal_cdf(cap + 0.5, mu, sigma) - _normal_cdf(floor - 0.5, mu, sigma)
    if cap is not None:
        return _normal_cdf(cap - 0.5, mu, sigma)
    if floor is not None:
        return 1 - _normal_cdf(floor + 0.5, mu, sigma)
    return None


class WeatherModel:
    def __init__(self, cfg: dict):
        self.sigma_base_f = float(cfg.get("sigma_base_f", 2.4))
        self.sigma_per_day_f = float(cfg.get("sigma_per_day_f", 1.0))
        self.cache_ttl = float(cfg.get("cache_ttl_sec", 1800))
        self.blend_market_weight = float(cfg.get("blend_market_weight", 0.15))
        # per-city forecast bias in the city's native unit: mean(observed -
        # forecast) from resolved markets; the report's weather section
        # suggests values once a city reaches n>=10 settled days
        self.city_bias = {str(k).lower(): float(v)
                          for k, v in (cfg.get("city_bias") or {}).items()}
        self.http = retrying_session()
        self._cache: dict[str, tuple[float, dict[str, float]]] = {}

    def _forecasts(self, city: str) -> dict[str, float]:
        cached = self._cache.get(city)
        if cached and time.monotonic() - cached[0] < self.cache_ttl:
            return cached[1]
        # batch-fetch every configured city in ONE request per unit —
        # per-city requests (30+/cycle, times retries) trip open-meteo's
        # rate limit and starve the whole cycle
        _, _, _, unit = CITIES[city]
        group = [(name, c) for name, c in CITIES.items() if c[3] == unit]
        now = time.monotonic()
        try:
            r = self.http.get(OPEN_METEO, params={
                "latitude": ",".join(str(c[0]) for _, c in group),
                "longitude": ",".join(str(c[1]) for _, c in group),
                "timezone": ",".join(c[2] for _, c in group),
                "daily": "temperature_2m_max", "temperature_unit": unit,
                "forecast_days": 16,
            }, timeout=30)
            r.raise_for_status()
            payload = r.json()
            results = payload if isinstance(payload, list) else [payload]
            for (name, _), loc in zip(group, results):
                daily = loc.get("daily", {})
                self._cache[name] = (now, {
                    d: t for d, t in zip(daily.get("time", []),
                                         daily.get("temperature_2m_max", []))
                    if t is not None})
        except Exception:  # noqa: BLE001 — a dead feed must not sink the cycle
            log.warning("weather batch fetch failed (%s unit)", unit, exc_info=True)
            for name, _ in group:  # cache the failure briefly: no per-market retries
                self._cache.setdefault(name, (now, {}))
        return self._cache.get(city, (now, {}))[1]

    def estimate(self, markets: list[SportsMarket]) -> list[FairEstimate]:
        estimates: list[FairEstimate] = []
        today = datetime.now(timezone.utc).date()
        for mkt in markets:
            year = mkt.end_date.year if mkt.end_date else today.year
            q = parse_question(mkt.question, year)
            if q is None or q.city not in CITIES:
                continue
            mu = self._forecasts(q.city).get(q.target.isoformat())
            if mu is None:
                continue
            mu += self.city_bias.get(q.city, 0.0)
            days_ahead = max(0, (q.target - today).days)
            sigma_f = self.sigma_base_f + self.sigma_per_day_f * days_ahead
            sigma = sigma_f * (5 / 9) if q.unit == "celsius" else sigma_f
            p = band_probability(mu, sigma, q.floor, q.cap)
            if p is None:
                continue
            for idx, outcome in enumerate(mkt.outcomes):
                if outcome.lower() not in ("yes", "no"):
                    continue
                prob = p if outcome.lower() == "yes" else 1 - p
                mid = mkt.outcome_prices[idx] if idx < len(mkt.outcome_prices) else None
                fair = prob
                if mid is not None and 0 < mid < 1:
                    w = self.blend_market_weight
                    fair = (1 - w) * prob + w * mid
                estimates.append(FairEstimate(
                    market=mkt, outcome_index=idx, outcome_name=outcome,
                    fair_prob=fair, consensus_prob=prob,
                    matched_game=f"weather:{q.city} {q.target} "
                                 f"[{q.floor},{q.cap}] mu={mu:.1f}±{sigma:.1f}",
                    n_books=0,
                ))
        log.info("weather model: %d estimates", len(estimates))
        return estimates
