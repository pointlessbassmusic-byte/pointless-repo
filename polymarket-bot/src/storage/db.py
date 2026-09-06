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
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def record_scan(self, n_markets: int, n_estimates: int, n_signals: int) -> None:
        self.conn.execute(
            "INSERT INTO scans (ts, n_markets, n_estimates, n_signals) VALUES (?,?,?,?)",
            (_now(), n_markets, n_estimates, n_signals),
        )
        self.conn.commit()

    def record_order(self, s, status: str) -> None:
        self.conn.execute(
            "INSERT INTO orders (ts, token_id, question, outcome, matched_game, fair_prob,"
            " ask, edge, stake_usd, shares, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), s.token_id, s.market_question, s.outcome_name, s.matched_game,
             s.fair_prob, s.ask, s.edge, s.stake_usd, s.shares, status),
        )
        self.conn.commit()
