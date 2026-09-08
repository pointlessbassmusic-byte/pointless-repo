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
CREATE TABLE IF NOT EXISTS settlements (
    ticker TEXT PRIMARY KEY,
    result TEXT NOT NULL,     -- 'yes' | 'no'
    ts TEXT NOT NULL
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
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def placed_tickers(self) -> set[str]:
        """Tickers that already have a live order placed — never re-order the same market."""
        rows = self.conn.execute(
            "SELECT DISTINCT ticker FROM orders WHERE status LIKE 'placed%'"
        ).fetchall()
        return {r[0] for r in rows}

    def live_exposure(self, days: int = 30) -> float:
        """USD committed to still-OPEN live orders; counts against
        max_total_exposure. Settled positions release their exposure."""
        row = self.conn.execute(
            "SELECT COALESCE(SUM(stake_usd), 0) FROM orders"
            " WHERE status LIKE 'placed%' AND ts >= datetime('now', ?)"
            " AND ticker NOT IN (SELECT ticker FROM settlements)",
            (f"-{int(days)} days",),
        ).fetchone()
        return float(row[0])

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

    def record_settlements(self, results: dict[str, str]) -> None:
        ts = _now()
        self.conn.executemany(
            "INSERT OR REPLACE INTO settlements (ticker, result, ts) VALUES (?,?,?)",
            [(t, r, ts) for t, r in results.items()],
        )
        self.conn.commit()

    def settled_outcomes(self) -> dict[str, float]:
        rows = self.conn.execute("SELECT ticker, result FROM settlements").fetchall()
        return {t: (1.0 if r == "yes" else 0.0) for t, r in rows}

    def unsettled_forecast_tickers(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT ticker FROM forecasts"
            " WHERE ticker NOT IN (SELECT ticker FROM settlements)"
        ).fetchall()
        return [r[0] for r in rows]

    def placed_unsettled_tickers(self) -> list[str]:
        """Tickers with live orders and no settlement yet — the set the daily-loss
        circuit breaker needs resolved every cycle."""
        rows = self.conn.execute(
            "SELECT DISTINCT ticker FROM orders WHERE status LIKE 'placed%'"
            " AND ticker NOT IN (SELECT ticker FROM settlements)"
        ).fetchall()
        return [r[0] for r in rows]

    def realized_pnl_today(self) -> float:
        """Realized PnL (USD) of live orders whose market settled today.

        Payout per contract is `outcome` for yes-side buys and `1 - outcome`
        for no-side buys; entry cost is the recorded price.
        """
        row = self.conn.execute(
            "SELECT COALESCE(SUM(((CASE WHEN o.side='yes' THEN s.result_val"
            "   ELSE 1 - s.result_val END) - o.price) * o.count), 0)"
            " FROM orders o JOIN"
            " (SELECT ticker, CASE result WHEN 'yes' THEN 1.0 ELSE 0.0 END AS result_val, ts"
            "  FROM settlements) s ON s.ticker = o.ticker"
            " WHERE o.status LIKE 'placed%' AND date(s.ts) = date('now')"
        ).fetchone()
        return float(row[0])

    def record_order(self, s, status: str) -> None:
        self.conn.execute(
            "INSERT INTO orders (ts, ticker, title, side, fair_prob, price, edge, stake_usd,"
            " count, rationale, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), s.ticker, s.title, s.side, s.fair_prob, s.price, s.edge,
             s.stake_usd, s.count, s.rationale, status),
        )
        self.conn.commit()
