"""Substrate milestone 2: Kalshi decision-time snapshot service for weather
dailies. Shadow mode, read-only, public endpoints only (no auth, no orders).

Persists the FIRST sighting of each market (maximum lead time = the
substrate protocol's decision point) plus periodic refreshes, then fills in
outcomes when markets settle. `export_ingest_csv` emits the substrate
ingest.py schema using the max-lead snapshot as market_prob.

baseline_prob caveat: until a real climatology/GenCast feed is wired, the
conventional baseline is emitted as 0.5 (no-skill stand-in). The market null
is unaffected; treat baseline-expert scores as placeholders and replace the
column when a climatology source lands.
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
CREATE INDEX IF NOT EXISTS idx_ws_ticker ON weather_snapshots(ticker, ts);
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
        snapshot as the decision-time market probability."""
        rows = self.conn.execute(
            """SELECT s.ticker, MIN(s.ts) AS first_ts, s.close_ts,
                      o.outcome, o.settled_ts
               FROM weather_snapshots s
               LEFT JOIN weather_outcomes o ON o.ticker = s.ticker
               GROUP BY s.ticker"""
        ).fetchall()
        written = 0
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
                w.writerow([
                    r["ticker"], "weather",
                    f"{r['first_ts']:.0f}",
                    f"{(r['settled_ts'] or r['close_ts'] or r['first_ts']):.0f}",
                    f"{mid:.4f}",
                    "0.5000",  # placeholder baseline; see module docstring
                    "" if r["outcome"] is None else int(r["outcome"]),
                ])
                written += 1
        return {"rows": written, "path": out_path}
