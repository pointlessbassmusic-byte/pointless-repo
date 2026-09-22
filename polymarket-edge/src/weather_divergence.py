"""Forecast vs market-implied temperature, city by city.

    python -m src.weather_divergence

Every city-day on Polymarket is a full set of whole-degree buckets, so the
bucket prices are a distribution whose mean is the market's expected high.
Comparing it against our open-meteo forecast separates the two things that
look identical in a signal list:

  * a real disagreement — a degree or so, inside forecast error, tradeable;
  * a mismatched input — several degrees, one sign, day after day, because our
    grid cell is not the station the market settles on.

The second kind is what `model.weather.city_bias` corrects, but that fit needs
settled outcomes (see `python -m src.report`). This view needs none: it reads
today's book, so a new city can be vetted before it has any history. Cities
flagged here are exactly the ones the model now abstains on
(`max_divergence_sigma`), so an empty flag list means nothing is being skipped.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from .clients.gamma import GammaClient
from .config import load_config
from .models.weather import CITIES, WeatherModel, market_implied_means, parse_question


def divergences(model: WeatherModel, markets: list) -> list[tuple]:
    """(target, city, model_mu, market_mu, diff, unit) per city-day, dated first."""
    today = datetime.now(timezone.utc).date()

    def year_of(mkt) -> int:
        return mkt.end_date.year if mkt.end_date else today.year

    implied = market_implied_means(markets, year_of)
    units: dict[str, str] = {}
    for mkt in markets:
        q = parse_question(mkt.question, year_of(mkt))
        if q is not None and q.city in CITIES:
            units[q.city] = "C" if q.unit == "celsius" else "F"

    rows = []
    for (city, target), mkt_mu in implied.items():
        if city not in CITIES:
            continue
        mu = model._forecasts(city).get(target.isoformat())
        if mu is None:
            continue
        mu += model.city_bias.get(city, 0.0)
        rows.append((target, city, mu, mkt_mu, mu - mkt_mu, units.get(city, "F")))
    return sorted(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="forecast vs market-implied temperature")
    ap.add_argument("--max-events", type=int, default=400)
    args = ap.parse_args()

    cfg = load_config()
    model = WeatherModel((cfg.model or {}).get("weather", {}))
    rows = divergences(model, GammaClient().weather_markets(max_events=args.max_events))
    if not rows:
        print("no city-day with a full bucket set and a forecast — nothing to compare")
        return

    print(f"{'date':<11}{'city':<14}{'model':>7}{'market':>8}{'diff':>7}  unit")
    for target, city, mu, mkt_mu, diff, unit in rows:
        print(f"{str(target):<11}{city:<14}{mu:>7.1f}{mkt_mu:>8.1f}{diff:>+7.1f}  {unit}")

    diffs = sorted(r[4] for r in rows)
    n = len(diffs)
    print(f"\nn={n}  median={diffs[n // 2]:+.2f}  "
          f"within 1 degree: {sum(1 for d in diffs if abs(d) <= 1)}/{n}")

    # one sign, several degrees, repeated across days = a mismatched station
    by_city: dict[str, list[float]] = {}
    for _, city, _, _, diff, _ in rows:
        by_city.setdefault(city, []).append(diff)
    suspect = {c: d for c, d in by_city.items()
               if len(d) >= 2 and min(abs(x) for x in d) >= 1.5
               and (all(x > 0 for x in d) or all(x < 0 for x in d))}
    if suspect:
        print("\nconsistently offset (candidate city_bias, confirm against settled "
              "outcomes in `python -m src.report` before applying):")
        for city, d in sorted(suspect.items()):
            print(f"  {city:<14} n={len(d)}  mean={sum(d) / len(d):+.2f}")


if __name__ == "__main__":
    main()
