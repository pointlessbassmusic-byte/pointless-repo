"""SQLite persistence: every cycle, every decision, every fill."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, mode TEXT,
    equity REAL, cash REAL, n_opportunities INTEGER, n_tradeable INTEGER
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, cycle_id INTEGER,
    strategy TEXT, label TEXT, gross_bps REAL, fee_bps REAL, net_bps REAL,
    max_notional REAL, action TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, strategy TEXT, label TEXT,
    notional REAL, gross_bps REAL, fee_bps REAL, net_bps REAL, pnl REAL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_dec_cycle ON decisions(cycle_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str = "data/cryptoarb.sqlite"):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def record_cycle(self, mode, equity, cash, n_opps, n_trade) -> int:
        cur = self.conn.execute(
            "INSERT INTO cycles (ts, mode, equity, cash, n_opportunities,"
            " n_tradeable) VALUES (?,?,?,?,?,?)",
            (now(), mode, equity, cash, n_opps, n_trade))
        self.conn.commit()
        return int(cur.lastrowid)

    def record_decision(self, cycle_id, opp, action, reason=""):
        self.conn.execute(
            "INSERT INTO decisions (ts, cycle_id, strategy, label, gross_bps,"
            " fee_bps, net_bps, max_notional, action, reason)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (now(), cycle_id, opp.strategy, opp.label, opp.gross_bps,
             opp.fee_bps, opp.net_bps, opp.max_notional, action,
             reason or opp.reason))
        self.conn.commit()

    def record_fill(self, f):
        self.conn.execute(
            "INSERT INTO fills (ts, strategy, label, notional, gross_bps,"
            " fee_bps, net_bps, pnl) VALUES (?,?,?,?,?,?,?,?)",
            (f.ts, f.strategy, f.label, f.notional, f.gross_bps, f.fee_bps,
             f.net_bps, f.pnl))
        self.conn.commit()

    def realized_by_strategy(self) -> dict:
        rows = self.conn.execute(
            "SELECT strategy, SUM(pnl) s FROM fills GROUP BY strategy").fetchall()
        return {r["strategy"]: r["s"] or 0.0 for r in rows}

    def recent_cycles(self, limit=240) -> list:
        rows = self.conn.execute(
            "SELECT * FROM cycles ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def recent_decisions(self, limit=40) -> list:
        rows = self.conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def best_net_series(self, limit=240) -> dict:
        """Best net edge per strategy per cycle — the 'how close to viable' chart."""
        rows = self.conn.execute(
            "SELECT cycle_id, strategy, MAX(net_bps) m FROM decisions"
            " GROUP BY cycle_id, strategy ORDER BY cycle_id DESC LIMIT ?",
            (limit * 3,)).fetchall()
        out: dict = {}
        for r in rows:
            out.setdefault(r["strategy"], []).append((r["cycle_id"], r["m"]))
        return {k: sorted(v) for k, v in out.items()}

    def set_kv(self, k, v):
        self.conn.execute("INSERT INTO kv (key,value) VALUES (?,?)"
                          " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (k, json.dumps(v, default=str)))
        self.conn.commit()

    def get_kv(self, k, default=None):
        r = self.conn.execute("SELECT value FROM kv WHERE key=?", (k,)).fetchone()
        return json.loads(r["value"]) if r else default
