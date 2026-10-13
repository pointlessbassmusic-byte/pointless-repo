"""Forecast vs market-implied temperature, station by station.

    python -m src.weather_divergence

Each KXHIGH*/KXLOWT* event is a full set of whole-degree strike bands, so the
band prices are a distribution whose mean is the market's expected extreme.
Comparing it against our open-meteo forecast separates two things that look
alike in a signal list:

  * a real disagreement — a degree or so, inside forecast error, tradeable;
  * a mismatched input — several degrees, one sign, day after day, because our
    grid cell is not the station Kalshi settles on.

The second kind is what `station.bias_f` corrects, and `weather_calibrate`
fits it from settled outcomes. This view needs none: it reads today's book, so
a station can be vetted before it has settled history. Stations flagged here
are the ones the generator now abstains on (`max_divergence_sigma`).

It also prints both sigmas. Ours much wider than the market's means every
narrow centre band looks overpriced to us and the arm sells it — a bet on
variance rather than on temperature, which settled outcomes have to settle.
"""
from __future__ import annotations

import argparse
import logging

from .client import KalshiClient
from .config import load_config
from .substrate.generators.weather import (DEFAULT_STATIONS, WeatherHigh,
                                           _event_date, market_implied_moments)

log = logging.getLogger(__name__)


def divergences(gen: WeatherHigh, markets: list) -> list[tuple]:
    """Per station-day, dated first:
    (target, prefix, model_mu, market_mu, diff, model_sigma, market_sigma)."""
    events: dict[str, str] = {}      # event_ticker -> station prefix
    for m in markets:
        event = m.event_ticker or ""
        prefix = event.split("-", 1)[0]
        if prefix in gen.stations:
            events[event] = prefix

    rows = []
    for event, prefix in events.items():
        target = _event_date(event)
        moments = market_implied_moments(markets, event)
        if target is None or moments is None:
            continue
        mkt_mu, mkt_sd = moments
        station = gen.stations[prefix]
        mu = gen._forecasts(prefix, station).get(target.isoformat())
        if mu is None:
            continue
        mu += float(station.get("bias_f", 0.0))
        lead = (target - gen._local_now(prefix).date()).days
        rows.append((target, prefix, mu, mkt_mu, mu - mkt_mu, gen.sigma_for(lead), mkt_sd))
    return sorted(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="forecast vs market-implied temperature")
    ap.add_argument("--max-markets", type=int, default=400,
                    help="per station series")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    cfg = load_config()
    client = KalshiClient(cfg.api_key_id, cfg.private_key_path, demo=cfg.use_demo,
                          read_prod=cfg.read_prod)
    gen = WeatherHigh((cfg.substrate.get("generators") or {}).get("weather", {}))

    markets = []
    for prefix in gen.stations:
        try:
            markets.extend(client.markets(statuses=["open"], series_ticker=prefix,
                                          max_markets=args.max_markets))
        except Exception:  # noqa: BLE001 — one dead series must not sink the table
            log.warning("could not read series %s", prefix, exc_info=True)
    rows = divergences(gen, markets)
    if not rows:
        print("no station-day with a full band set and a forecast — nothing to compare")
        return

    print(f"{'date':<11}{'station':<14}{'model':>7}{'market':>8}{'diff':>7}"
          f"{'our sd':>8}{'mkt sd':>8}{'ratio':>7}")
    for target, prefix, mu, mkt_mu, diff, sigma, mkt_sd in rows:
        ratio = sigma / mkt_sd if mkt_sd > 0 else float("inf")
        print(f"{str(target):<11}{prefix:<14}{mu:>7.1f}{mkt_mu:>8.1f}{diff:>+7.1f}"
              f"{sigma:>8.2f}{mkt_sd:>8.2f}{ratio:>7.1f}")

    diffs = sorted(r[4] for r in rows)
    n = len(diffs)
    print(f"\nn={n}  median={diffs[n // 2]:+.2f}F  "
          f"within 1F: {sum(1 for d in diffs if abs(d) <= 1)}/{n}")

    ratios = sorted(r[5] / r[6] for r in rows if r[6] > 0)
    if ratios:
        m = ratios[len(ratios) // 2]
        print(f"sigma ratio (ours / market-implied): median={m:.2f}  "
              f"range={ratios[0]:.2f}-{ratios[-1]:.2f}")

    # one sign, several degrees, on more than one day = a mismatched station
    by_station: dict[str, list[float]] = {}
    for _, prefix, _, _, diff, _, _ in rows:
        by_station.setdefault(prefix, []).append(diff)
    suspect = {s: d for s, d in by_station.items()
               if len(d) >= 2 and min(abs(x) for x in d) >= 1.5
               and (all(x > 0 for x in d) or all(x < 0 for x in d))}
    if suspect:
        print("\nconsistently offset (candidate station bias_f, confirm against "
              "settled outcomes with `python -m src.weather_calibrate`):")
        for prefix, d in sorted(suspect.items()):
            print(f"  {prefix:<14} n={len(d)}  mean={sum(d) / len(d):+.2f}F")
    print(f"\nstations configured: {len(DEFAULT_STATIONS)}; "
          f"station-days compared: {n}")


if __name__ == "__main__":
    main()
