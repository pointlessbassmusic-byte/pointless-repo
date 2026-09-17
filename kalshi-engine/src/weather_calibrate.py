"""Empirically calibrate the weather generator's sigma parameters.

The generator models the daily high as Normal(forecast, sigma_base + slope*lead).
Those two numbers should come from measured forecast error, not priors. Two
data sources, best available wins:

1. open-meteo's previous-runs API (instant ~60-day history of what the model
   predicted 1-7 days ahead vs its day-0 analysis). Blocked by some egress
   proxies — works on the deployment server.
2. Self-logged: each run appends today's forecast curve to weather_log, and
   scores past targets against Kalshi's own settled bands (the YES band's
   midpoint IS the observed high). Run daily (cron/systemd timer or alongside
   the engine) and the dataset builds itself.

Usage:
    python -m src.weather_calibrate           # log + score + fit + suggest
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, timezone

from .client import KalshiClient, Market
from .config import load_config
from .storage.db import Database
from .substrate.generators.weather import DEFAULT_STATIONS, WeatherHigh

PREV_RUNS_BASE = "https://previous-runs-api.open-meteo.com/v1/forecast"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def fit_sigma(samples: list[tuple[int, float]],
              min_per_lead: int = 5) -> tuple[float, float] | None:
    """(sigma_base, sigma_per_day) from (lead_days, error_f) samples: the
    stddev of error per lead, then a least-squares line through the stddevs."""
    by_lead: dict[int, list[float]] = defaultdict(list)
    for lead, err in samples:
        by_lead[lead].append(err)
    points = [(lead, statistics.stdev(errs))
              for lead, errs in sorted(by_lead.items()) if len(errs) >= min_per_lead]
    if len(points) < 2:
        return None
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    denom = sum((x - mx) ** 2 for x, _ in points)
    if denom == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in points) / denom
    base = my - slope * mx
    return max(0.5, round(base, 2)), max(0.0, round(slope, 2))


def observed_from_markets(markets: list[Market]) -> float | None:
    """Observed high implied by a settled daily-high event: the midpoint of the
    band that resolved YES. A YES on an open-ended threshold market only bounds
    the temperature, so those events are skipped (censored)."""
    for m in markets:
        if m.result == "yes" and m.floor_strike is not None and m.cap_strike is not None:
            return (m.floor_strike + m.cap_strike) / 2
    return None


def event_ticker_for(station: str, target: date) -> str:
    return f"{station}-{target.strftime('%y')}{MONTHS[target.month - 1]}{target.day:02d}"


def log_today(db: Database, gen: WeatherHigh) -> int:
    """Append today's forecast curve for every station; idempotent per day."""
    n = 0
    for station, cfg in gen.stations.items():
        curve = gen._forecasts(station, cfg)
        if not curve:
            continue
        day0 = min(curve)
        for lead, (target, temp) in enumerate(sorted(curve.items())):
            db.conn.execute(
                "INSERT OR IGNORE INTO weather_log"
                " (logged_date, station, target_date, lead_days, forecast_f)"
                " VALUES (?,?,?,?,?)", (day0, station, target, lead, temp))
            n += 1
    db.conn.commit()
    return n


def score_pending(db: Database, client: KalshiClient) -> int:
    """Resolve observed highs (from settled Kalshi bands) for logged targets."""
    today = datetime.now(timezone.utc).date().isoformat()
    pending = db.conn.execute(
        "SELECT DISTINCT station, target_date FROM weather_log"
        " WHERE target_date < ? AND (station, target_date) NOT IN"
        " (SELECT station, target_date FROM weather_observed)", (today,)).fetchall()
    n = 0
    for station, target_iso in pending:
        target = date.fromisoformat(target_iso)
        obs = observed_from_markets(client.markets_by_event(event_ticker_for(station, target)))
        if obs is not None:
            db.conn.execute(
                "INSERT OR REPLACE INTO weather_observed (station, target_date, observed_f)"
                " VALUES (?,?,?)", (station, target_iso, obs))
            n += 1
    db.conn.commit()
    return n


def self_logged_samples(db: Database) -> list[tuple[int, float]]:
    rows = db.conn.execute(
        "SELECT l.lead_days, l.forecast_f - o.observed_f FROM weather_log l"
        " JOIN weather_observed o ON o.station = l.station"
        " AND o.target_date = l.target_date").fetchall()
    return [(int(lead), float(err)) for lead, err in rows]


def station_bias(db: Database, min_n: int = 10) -> list[tuple[str, int, float]]:
    """Per-station (station, n, mean observed-forecast) from settled truth.

    A persistent nonzero mean is siting bias (grid cell vs the settlement
    station) — corrected by the generator's per-station bias_f, not by sigma.
    Only rows with n >= min_n are confident enough to act on.
    """
    rows = db.conn.execute(
        "SELECT l.station, COUNT(*), AVG(o.observed_f - l.forecast_f)"
        " FROM weather_log l JOIN weather_observed o"
        " ON o.station = l.station AND o.target_date = l.target_date"
        " GROUP BY l.station ORDER BY l.station").fetchall()
    return [(s, int(n), round(float(b), 2)) for s, n, b in rows]


def previous_runs_samples(gen: WeatherHigh, past_days: int = 60) -> list[tuple[int, float]]:
    """Instant history where the previous-runs API is reachable: forecast made
    N days ahead vs the model's day-0 analysis for the same date."""
    from .substrate.generators.weather import _daily_variable

    leads = range(1, 8)
    samples: list[tuple[int, float]] = []
    for station, cfg in gen.stations.items():
        var = _daily_variable(cfg)
        daily_vars = [var] + [f"{var}_previous_day{k}" for k in leads]
        try:
            r = gen.http.get(PREV_RUNS_BASE, params={
                "latitude": cfg["latitude"], "longitude": cfg["longitude"],
                "daily": ",".join(daily_vars), "temperature_unit": "fahrenheit",
                "past_days": past_days, "forecast_days": 1,
                "timezone": cfg.get("timezone", "UTC"),
            }, timeout=20)
            r.raise_for_status()
            daily = r.json().get("daily", {})
        except Exception:  # noqa: BLE001 — unreachable behind some proxies; fall back
            return []
        truth = daily.get(var) or []
        for k in leads:
            fc = daily.get(f"{var}_previous_day{k}") or []
            samples.extend((k, f - t) for f, t in zip(fc, truth)
                           if f is not None and t is not None)
    return samples


def main() -> None:
    cfg = load_config()
    db = Database(cfg.db_path)
    wcfg = (cfg.substrate.get("generators") or {}).get("weather") or {}
    gen = WeatherHigh({**wcfg, "stations": wcfg.get("stations") or DEFAULT_STATIONS})
    client = KalshiClient(demo=cfg.use_demo, read_prod=cfg.read_prod)

    print(f"logged {log_today(db, gen)} forecast points for today")
    print(f"scored {score_pending(db, client)} past targets against settled Kalshi bands")

    samples = previous_runs_samples(gen)
    source = "previous-runs API (60-day history)"
    if not samples:
        samples = self_logged_samples(db)
        source = f"self-logged data ({len(samples)} samples so far)"
        print("previous-runs API unreachable here (works without an egress proxy, "
              "e.g. on the server); using self-logged history")

    biases = station_bias(db)
    if biases:
        print("\nper-station bias (mean observed - forecast, from settled truth):")
        for st, n, b in biases:
            note = (f"  -> set stations.{st}.bias_f: {b}" if n >= 10
                    else "  (n < 10: watch, don't act yet)")
            print(f"  {st:<13} n={n:<4} bias={b:+.2f}F{note}")
        print("  (sigma from the previous-runs API excludes siting bias; bias_f"
              " comes only from this Kalshi-settled table)")

    fitted = fit_sigma(samples)
    if fitted is None:
        print(f"not enough data to fit sigma yet from {source} — "
              "run this daily and the dataset builds itself")
        return
    base, slope = fitted
    print(f"\nempirical fit from {source}:")
    print(f"  suggested config.yaml values ->  sigma_base_f: {base}   sigma_per_day_f: {slope}")
    print(f"  (current: sigma_base_f: {gen.sigma_base}   sigma_per_day_f: {gen.sigma_per_day})")


if __name__ == "__main__":
    main()
