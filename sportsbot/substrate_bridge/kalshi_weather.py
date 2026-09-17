"""Substrate milestone 2: Kalshi decision-time snapshot service for weather
dailies. Shadow mode, read-only, public endpoints only (no auth, no orders).

Persists the FIRST sighting of each market (maximum lead time = the
substrate protocol's decision point) plus periodic refreshes, then fills in
outcomes when markets settle. `export_ingest_csv` emits the substrate
ingest.py schema using the max-lead snapshot as market_prob.

baseline_prob is the station climatology (climatology.py: NOAA NCEI TMAX,
±7-day day-of-year window, prior years only — leak-proof by construction),
falling back to the 0.5 no-skill stand-in when a station or title can't be
resolved. Measured on the first settled cohort: coin 0.25 → climatology
~0.19 → market ~0.05 Brier — the triple-null hierarchy the protocol expects.
"""

from __future__ import annotations

import csv
import logging
import os
import sqlite3
import time
from typing import Optional

from sportsbot.exchanges.kalshi import API_ROOT, KalshiClient

log = logging.getLogger(__name__)

# High-temperature daily series at named settlement stations (NWS climate
# reports). Discovery by category is attempted first; this list is the
# fallback and the default allowlist.
DEFAULT_SERIES = [
    "KXHIGHNY", "KXHIGHCHI", "KXHIGHLAX", "KXHIGHDEN",
    "KXHIGHMIA", "KXHIGHAUS", "KXHIGHPHIL",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS weather_snapshots (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    ticker TEXT NOT NULL,
    series TEXT,
    title TEXT,
    yes_bid REAL,
    yes_ask REAL,
    close_ts REAL,
    UNIQUE(ticker, ts)
);
CREATE TABLE IF NOT EXISTS weather_outcomes (
    ticker TEXT PRIMARY KEY,
    outcome INTEGER,
    settled_ts REAL
);
CREATE TABLE IF NOT EXISTS weather_forecasts (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    series TEXT NOT NULL,
    target_date TEXT NOT NULL,
    forecast_high INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ws_ticker ON weather_snapshots(ticker, ts);
CREATE INDEX IF NOT EXISTS idx_wf_series_date ON weather_forecasts(series, target_date, ts);
"""


class WeatherSnapshotService:
    def __init__(self, client: Optional[KalshiClient] = None,
                 db_path: str = "data/weather_snapshots.sqlite",
                 series: Optional[list[str]] = None) -> None:
        self.client = client or KalshiClient(env="prod")
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.series = series or DEFAULT_SERIES

    # ------------------------------------------------------------------
    def snapshot_once(self) -> dict:
        """One pass over all series: record open-market quotes, settle
        previously tracked tickers. Returns a summary."""
        recorded = 0
        now = time.time()
        self._record_forecasts(now)   # BEFORE market rows: forecast ts <= snapshot ts
        for series in self.series:
            try:
                data = self.client._request(
                    "GET", f"{API_ROOT}/markets",
                    params={"series_ticker": series, "status": "open",
                            "mve_filter": "exclude", "limit": 200},
                )
            except Exception as exc:
                log.warning("weather snapshot failed for %s: %s", series, exc)
                continue
            for m in data.get("markets", []):
                bid = self.client._dollars(m, "yes_bid_dollars")
                ask = self.client._dollars(m, "yes_ask_dollars")
                close = self.client._ts(m, "close_time", "expected_expiration_time")
                try:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO weather_snapshots"
                        " (ts, ticker, series, title, yes_bid, yes_ask, close_ts)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (now, m.get("ticker"), series, m.get("title"),
                         bid, ask, close.timestamp() if close else None),
                    )
                    recorded += 1
                except sqlite3.Error:
                    log.exception("snapshot insert failed")
        self.conn.commit()
        settled = self._resolve_outcomes()
        return {"recorded": recorded, "settled": settled}

    def _record_forecasts(self, now: float) -> None:
        """Record NWS point-forecast highs for each series' settlement station.
        Runs before market snapshots so every market's first sighting has a
        decision-time forecast on file (never backfilled)."""
        try:
            from sportsbot.signals import nws
        except ImportError:
            return
        for series in self.series:
            try:
                highs = nws.forecast_highs(series)
            except Exception as exc:  # noqa: BLE001 — forecast feed down != no snapshots
                log.warning("nws forecast failed for %s: %s", series, exc)
                continue
            for target, high in highs.items():
                self.conn.execute(
                    "INSERT INTO weather_forecasts (ts, series, target_date,"
                    " forecast_high) VALUES (?,?,?,?)",
                    (now, series, target.isoformat(), int(high)))
        self.conn.commit()

    def _resolve_outcomes(self, max_calls: int = 100) -> int:
        rows = self.conn.execute(
            """SELECT DISTINCT s.ticker FROM weather_snapshots s
               LEFT JOIN weather_outcomes o ON o.ticker = s.ticker
               WHERE o.ticker IS NULL AND s.close_ts IS NOT NULL
                 AND s.close_ts < ?""",
            (time.time(),),
        ).fetchall()
        settled = 0
        for row in rows[:max_calls]:
            res = self.client.get_resolution(row["ticker"])
            if res is None:
                continue
            self.conn.execute(
                "INSERT OR REPLACE INTO weather_outcomes (ticker, outcome, settled_ts)"
                " VALUES (?,?,?)",
                (row["ticker"], int(res), time.time()),
            )
            settled += 1
        self.conn.commit()
        return settled

    def run_loop(self, interval_seconds: float = 3600.0) -> None:
        log.info("weather snapshot loop: %s every %ss", self.series, interval_seconds)
        while True:
            started = time.time()
            try:
                summary = self.snapshot_once()
                log.info("weather snapshot: %s", summary)
            except Exception:
                log.exception("weather snapshot cycle failed")
            time.sleep(max(30.0, interval_seconds - (time.time() - started)))

    # ------------------------------------------------------------------
    def export_ingest_csv(self, out_path: str) -> dict:
        """Emit substrate ingest schema using each ticker's max-lead (first)
        snapshot as the decision-time market probability. baseline_prob is the
        station climatology (prior years only — see climatology.py's no-leak
        rule) and falls back to the 0.5 no-skill stand-in when the station or
        title can't be resolved."""
        from sportsbot.substrate_bridge.climatology import Climatology, parse_market

        climo = Climatology()
        try:
            from sportsbot.signals.nws import prob_from_high, sigma_for_lead
        except ImportError:
            prob_from_high = None

        def nws_baseline(series: str, title: str, first_ts: float):
            """Forecast-based P(YES) from the earliest forecast recorded AT OR
            BEFORE the market's first snapshot — never a later one. Sigma
            widens with the lead between decision time and the target day."""
            if prob_from_high is None:
                return None
            parsed = parse_market(title)
            if parsed is None:
                return None
            row = self.conn.execute(
                "SELECT forecast_high FROM weather_forecasts"
                " WHERE series=? AND target_date=? AND ts <= ?"
                " ORDER BY ts ASC LIMIT 1",
                (series, parsed[0].isoformat(), first_ts + 1.0)).fetchone()
            if row is None:
                return None
            from datetime import datetime, timezone
            lead_days = (datetime.combine(parsed[0], datetime.min.time(),
                                          tzinfo=timezone.utc).timestamp()
                         - first_ts) / 86400.0
            return prob_from_high(title, float(row["forecast_high"]),
                                  sigma=sigma_for_lead(lead_days))
        rows = self.conn.execute(
            """SELECT s.ticker, s.series, s.title, MIN(s.ts) AS first_ts,
                      s.close_ts, o.outcome, o.settled_ts
               FROM weather_snapshots s
               LEFT JOIN weather_outcomes o ON o.ticker = s.ticker
               GROUP BY s.ticker"""
        ).fetchall()
        written = climo_rows = nws_rows = 0
        with open(out_path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["event_id", "domain", "close_time", "resolve_time",
                        "market_prob", "baseline_prob", "outcome"])
            for r in rows:
                snap = self.conn.execute(
                    "SELECT yes_bid, yes_ask FROM weather_snapshots"
                    " WHERE ticker=? AND ts=?",
                    (r["ticker"], r["first_ts"]),
                ).fetchone()
                if snap is None or snap["yes_bid"] is None or snap["yes_ask"] is None:
                    continue
                mid = (snap["yes_bid"] + snap["yes_ask"]) / 2.0
                baseline = nws_baseline(r["series"], r["title"], r["first_ts"])
                if baseline is not None:
                    nws_rows += 1
                else:
                    baseline = climo.prob(r["series"], r["title"])
                    if baseline is not None:
                        climo_rows += 1
                w.writerow([
                    r["ticker"], "weather",
                    f"{r['first_ts']:.0f}",
                    f"{(r['settled_ts'] or r['close_ts'] or r['first_ts']):.0f}",
                    f"{mid:.4f}",
                    f"{(0.5 if baseline is None else baseline):.4f}",
                    "" if r["outcome"] is None else int(r["outcome"]),
                ])
                written += 1
        return {"rows": written, "path": out_path,
                "nws_rows": nws_rows, "climatology_rows": climo_rows}
