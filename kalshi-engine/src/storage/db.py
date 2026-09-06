"""SQLite storage: scans, price history (fuels mean-reversion), forecasts, orders."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    n_markets INTEGER,
    n_forecasts INTEGER,
    n_signals INTEGER
);
CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    mid REAL
);
CREATE INDEX IF NOT EXISTS idx_prices_ticker_ts ON prices (ticker, ts);
CREATE TABLE IF NOT EXISTS forecasts (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    generator TEXT,
    prob_yes REAL,
    confidence REAL,
    rationale TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    ticker TEXT,
    title TEXT,
    side TEXT,
    fair_prob REAL,
    price REAL,
    edge REAL,
    stake_usd REAL,
    count INTEGER,
    rationale TEXT,
    status TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def record_scan(self, n_markets: int, n_forecasts: int, n_signals: int) -> None:
        self.conn.execute(
            "INSERT INTO scans (ts, n_markets, n_forecasts, n_signals) VALUES (?,?,?,?)",
            (_now(), n_markets, n_forecasts, n_signals),
        )
        self.conn.commit()

    def record_prices(self, mids: dict[str, float]) -> None:
        ts = _now()
        self.conn.executemany(
            "INSERT INTO prices (ts, ticker, mid) VALUES (?,?,?)",
            [(ts, t, m) for t, m in mids.items()],
        )
        self.conn.commit()

    def price_history(self, tickers: list[str], max_points: int = 50) -> dict[str, list[tuple[str, float]]]:
        out: dict[str, list[tuple[str, float]]] = {}
        for t in tickers:
            rows = self.conn.execute(
                "SELECT ts, mid FROM prices WHERE ticker=? ORDER BY ts DESC LIMIT ?",
                (t, max_points),
            ).fetchall()
            out[t] = list(reversed(rows))
        return out

    def record_forecasts(self, ticker: str, forecasts) -> None:
        ts = _now()
        self.conn.executemany(
            "INSERT INTO forecasts (ts, ticker, generator, prob_yes, confidence, rationale)"
            " VALUES (?,?,?,?,?,?)",
            [(ts, ticker, f.generator, f.prob_yes, f.confidence, f.rationale) for f in forecasts],
        )
        self.conn.commit()

    def record_order(self, s, status: str) -> None:
        self.conn.execute(
            "INSERT INTO orders (ts, ticker, title, side, fair_prob, price, edge, stake_usd,"
            " count, rationale, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), s.ticker, s.title, s.side, s.fair_prob, s.price, s.edge,
             s.stake_usd, s.count, s.rationale, status),
        )
        self.conn.commit()
