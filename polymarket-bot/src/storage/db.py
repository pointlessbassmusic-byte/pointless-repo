"""SQLite storage: scan snapshots, signals, and order intents/results."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    n_markets INTEGER,
    n_estimates INTEGER,
    n_signals INTEGER
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    token_id TEXT,
    question TEXT,
    outcome TEXT,
    matched_game TEXT,
    fair_prob REAL,
    ask REAL,
    edge REAL,
    stake_usd REAL,
    shares REAL,
    status TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_token_status ON orders (token_id, status);
CREATE TABLE IF NOT EXISTS settlements (
    token_id TEXT PRIMARY KEY,
    outcome REAL NOT NULL,    -- 1.0 | 0.0
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS estimates (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    token_id TEXT NOT NULL,
    condition_id TEXT,
    question TEXT,
    outcome TEXT,
    matched_game TEXT,
    fair_prob REAL,
    consensus_prob REAL,
    n_books INTEGER,
    bid REAL,
    ask REAL
);
CREATE INDEX IF NOT EXISTS idx_estimates_token_ts ON estimates (token_id, ts);
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
        # migrate estimates tables created before condition_id existed
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(estimates)")}
        if "condition_id" not in cols:
            self.conn.execute("ALTER TABLE estimates ADD COLUMN condition_id TEXT")
        self.conn.commit()

    def record_scan(self, n_markets: int, n_estimates: int, n_signals: int) -> None:
        self.conn.execute(
            "INSERT INTO scans (ts, n_markets, n_estimates, n_signals) VALUES (?,?,?,?)",
            (_now(), n_markets, n_estimates, n_signals),
        )
        self.conn.commit()

    def record_estimates(self, estimates, quotes) -> None:
        """Snapshot every fair-value estimate + quote for later calibration analysis."""
        ts = _now()
        rows = []
        for e in estimates:
            token_id = e.market.clob_token_ids[e.outcome_index]
            q = quotes.get(token_id)
            rows.append((
                ts, token_id, e.market.condition_id, e.market.question, e.outcome_name,
                e.matched_game, e.fair_prob, e.consensus_prob, e.n_books,
                q.bid if q else None, q.ask if q else None,
            ))
        self.conn.executemany(
            "INSERT INTO estimates (ts, token_id, condition_id, question, outcome, matched_game,"
            " fair_prob, consensus_prob, n_books, bid, ask) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()

    def record_settlements(self, outcomes: dict[str, float]) -> None:
        ts = _now()
        self.conn.executemany(
            "INSERT OR REPLACE INTO settlements (token_id, outcome, ts) VALUES (?,?,?)",
            [(t, o, ts) for t, o in outcomes.items()],
        )
        self.conn.commit()

    def settled_outcomes(self) -> dict[str, float]:
        rows = self.conn.execute("SELECT token_id, outcome FROM settlements").fetchall()
        return dict(rows)

    def unsettled_condition_ids(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT condition_id FROM estimates WHERE condition_id IS NOT NULL"
            " AND condition_id != ''"
            " AND token_id NOT IN (SELECT token_id FROM settlements)"
        ).fetchall()
        return [r[0] for r in rows]

    def placed_tokens(self) -> set[str]:
        """Token ids that already have a live order placed — never order twice."""
        rows = self.conn.execute(
            "SELECT DISTINCT token_id FROM orders WHERE status LIKE 'placed%'"
        ).fetchall()
        return {r[0] for r in rows}

    def live_exposure(self, days: int = 7) -> float:
        """USD committed to live orders recently; counts against max_total_exposure."""
        row = self.conn.execute(
            "SELECT COALESCE(SUM(stake_usd), 0) FROM orders"
            " WHERE status LIKE 'placed%' AND ts >= datetime('now', ?)",
            (f"-{int(days)} days",),
        ).fetchone()
        return float(row[0])

    def record_order(self, s, status: str) -> None:
        self.conn.execute(
            "INSERT INTO orders (ts, token_id, question, outcome, matched_game, fair_prob,"
            " ask, edge, stake_usd, shares, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), s.token_id, s.market_question, s.outcome_name, s.matched_game,
             s.fair_prob, s.ask, s.edge, s.stake_usd, s.shares, status),
        )
        self.conn.commit()
