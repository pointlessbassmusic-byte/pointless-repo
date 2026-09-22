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
from .models.weather import (CITIES, WeatherModel, market_implied_moments,
                             parse_question)


def divergences(model: WeatherModel, markets: list) -> list[tuple]:
    """Per city-day, dated first:
    (target, city, model_mu, market_mu, diff, model_sigma, market_sigma, unit)."""
    today = datetime.now(timezone.utc).date()

    def year_of(mkt) -> int:
        return mkt.end_date.year if mkt.end_date else today.year

    implied = market_implied_moments(markets, year_of)
    units: dict[str, str] = {}
    for mkt in markets:
        q = parse_question(mkt.question, year_of(mkt))
        if q is not None and q.city in CITIES:
            units[q.city] = "C" if q.unit == "celsius" else "F"

    rows = []
    for (city, target), (mkt_mu, mkt_sd) in implied.items():
        if city not in CITIES:
            continue
        mu = model._forecasts(city).get(target.isoformat())
        if mu is None:
            continue
        mu += model.city_bias.get(city, 0.0)
        unit = units.get(city, "F")
        lead = (target - model._local_now(city).date()).days
        sigma = model.sigma_for(lead, "celsius" if unit == "C" else "fahrenheit")
        rows.append((target, city, mu, mkt_mu, mu - mkt_mu, sigma, mkt_sd, unit))
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

    print(f"{'date':<11}{'city':<14}{'model':>7}{'market':>8}{'diff':>7}"
          f"{'our sd':>8}{'mkt sd':>8}{'ratio':>7}  unit")
    for target, city, mu, mkt_mu, diff, sigma, mkt_sd, unit in rows:
        ratio = sigma / mkt_sd if mkt_sd > 0 else float("inf")
        print(f"{str(target):<11}{city:<14}{mu:>7.1f}{mkt_mu:>8.1f}{diff:>+7.1f}"
              f"{sigma:>8.2f}{mkt_sd:>8.2f}{ratio:>7.1f}  {unit}")

    diffs = sorted(r[4] for r in rows)
    n = len(diffs)
    print(f"\nn={n}  median={diffs[n // 2]:+.2f}  "
          f"within 1 degree: {sum(1 for d in diffs if abs(d) <= 1)}/{n}")

    # how our claimed uncertainty compares with the market's. Ours much wider
    # means every narrow centre bucket looks overpriced to us and we sell it —
    # a bet on variance, not on temperature. The market sd is a floor (tails are
    # pulled in), so a ratio near 1 already means we are the wider one.
    ratios = sorted(r[5] / r[6] for r in rows if r[6] > 0)
    if ratios:
        m = ratios[len(ratios) // 2]
        print(f"sigma ratio (ours / market-implied): median={m:.2f}  "
              f"range={ratios[0]:.2f}-{ratios[-1]:.2f}")
        if m > 1.3:
            print("  our sigma is the wider one: expect systematic NO signals on "
                  "narrow centre buckets. Settle-score before trusting them "
                  "(`python -m src.report`).")

    # one sign, several degrees, repeated across days = a mismatched station
    by_city: dict[str, list[float]] = {}
    for _, city, _, _, diff, _, _, _ in rows:
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
