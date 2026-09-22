"""SQLite persistence: bets, orders, fills, predictions, and market closes
for CLV. One file, WAL mode, safe for the single-process bot.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    market_id TEXT NOT NULL,
    sport TEXT,
    model TEXT,
    prob_yes REAL,
    prob_raw REAL,
    features TEXT
);
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    market_id TEXT NOT NULL,
    sport TEXT,
    side TEXT,
    model_prob REAL,
    entry_price REAL,
    stake REAL,
    size REAL,
    edge REAL,
    exchange TEXT,
    mode TEXT,
    closing_price REAL,
    outcome INTEGER,
    pnl REAL,
    status TEXT,
    UNIQUE(market_id, side, ts)
);
CREATE TABLE IF NOT EXISTS orders (
    client_id TEXT PRIMARY KEY,
    order_id TEXT,
    ts TEXT NOT NULL,
    market_id TEXT,
    side TEXT,
    price REAL,
    size REAL,
    filled REAL,
    status TEXT,
    raw TEXT
);
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    market_id TEXT NOT NULL,
    bid REAL,
    ask REAL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    account TEXT NOT NULL,
    market_id TEXT NOT NULL,
    sport TEXT,
    title TEXT,
    action TEXT NOT NULL,
    side TEXT,
    model_prob REAL,
    market_prob REAL,
    price REAL,
    edge REAL,
    stake REAL,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    account TEXT NOT NULL,
    cash REAL,
    exposure REAL,
    equity REAL,
    realized_pnl REAL,
    open_positions INTEGER
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(account, ts);
CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_snapshots(account, ts);
CREATE INDEX IF NOT EXISTS idx_bets_market ON bets(market_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_market ON market_snapshots(market_id, ts);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str = "data/sportsbot.sqlite") -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(bets)")]
        if "status" not in cols:  # migrate pre-position-management DBs
            self.conn.execute("ALTER TABLE bets ADD COLUMN status TEXT")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- writes ----------------------------------------------------------
    def record_prediction(self, market_id: str, sport: str, model: str,
                          prob_yes: float, prob_raw: Optional[float],
                          features: dict | None = None) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO predictions (ts, market_id, sport, model, prob_yes, prob_raw, features)"
                " VALUES (?,?,?,?,?,?,?)",
                (_now(), market_id, sport, model, prob_yes, prob_raw,
                 json.dumps(features or {}, default=str)),
            )
            self.conn.commit()

    def record_bet(self, market_id: str, sport: str, side: str, model_prob: float,
                   entry_price: float, stake: float, size: float, edge: float,
                   exchange: str, mode: str) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO bets (ts, market_id, sport, side, model_prob, entry_price,"
                " stake, size, edge, exchange, mode) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), market_id, sport, side, model_prob, entry_price,
                 stake, size, edge, exchange, mode),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def settle_bet(self, bet_id: int, outcome: int, pnl: float,
                   closing_price: Optional[float] = None) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE bets SET outcome=?, pnl=?, closing_price=COALESCE(?, closing_price)"
                " WHERE id=?",
                (outcome, pnl, closing_price, bet_id),
            )
            self.conn.commit()

    def close_bet(self, bet_id: int, pnl: float,
                  closing_price: Optional[float] = None) -> None:
        """Early exit: position sold before resolution. `outcome` stays NULL
        (no win/lose observation → excluded from Brier/calibration), but the
        realized pnl counts toward daily loss, drawdown, and PnL totals."""
        with self._lock:
            self.conn.execute(
                "UPDATE bets SET status='closed', pnl=?,"
                " closing_price=COALESCE(?, closing_price) WHERE id=?",
                (pnl, closing_price, bet_id),
            )
            self.conn.commit()

    def record_decision(self, account: str, market_id: str, action: str,
                        sport: str | None = None, title: str | None = None,
                        side: str | None = None, model_prob: float | None = None,
                        market_prob: float | None = None, price: float | None = None,
                        edge: float | None = None, stake: float | None = None,
                        reason: str | None = None) -> None:
        """One line of the bot's reasoning: what it looked at and what it did.

        `action` is 'bet', 'skip' or 'exit'. Skips carry the reason they were
        skipped — the decision feed is only honest if the passes are in it too,
        since on a near-efficient slate almost every decision is a pass.
        """
        with self._lock:
            self.conn.execute(
                "INSERT INTO decisions (ts, account, market_id, sport, title, action,"
                " side, model_prob, market_prob, price, edge, stake, reason)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), account, market_id, sport, title, action, side,
                 model_prob, market_prob, price, edge, stake, reason))
            self.conn.commit()

    def prune_decisions(self, keep: int = 5000,
                        account: str | None = None) -> int:
        """Keep the feed bounded — a 5-minute loop over a full slate writes
        thousands of passes a day and none are worth keeping forever.

        Pruning is PER ACCOUNT: a global cap lets the sim book's thousands of
        daily passes evict the real book's feed entirely, which is the one
        feed you would actually want kept.
        """
        with self._lock:
            if account is None:
                accounts = [r[0] for r in self.conn.execute(
                    "SELECT DISTINCT account FROM decisions")]
            else:
                accounts = [account]
            removed = 0
            for acct in accounts:
                cur = self.conn.execute(
                    "DELETE FROM decisions WHERE account=? AND id NOT IN"
                    " (SELECT id FROM decisions WHERE account=?"
                    "  ORDER BY id DESC LIMIT ?)", (acct, acct, keep))
                removed += cur.rowcount
            self.conn.commit()
            return removed

    def record_equity(self, account: str, cash: float, exposure: float,
                      equity: float, realized_pnl: float,
                      open_positions: int) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO equity_snapshots (ts, account, cash, exposure, equity,"
                " realized_pnl, open_positions) VALUES (?,?,?,?,?,?,?)",
                (_now(), account, cash, exposure, equity, realized_pnl,
                 open_positions))
            self.conn.commit()

    def recent_decisions(self, account: str, limit: int = 60) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM decisions WHERE account=? ORDER BY id DESC LIMIT ?",
            (account, limit)).fetchall()
        return [dict(r) for r in rows]

    def equity_series(self, account: str, limit: int = 1000) -> list[dict]:
        """Oldest-first, so it plots as a curve without the caller reversing it."""
        rows = self.conn.execute(
            "SELECT * FROM equity_snapshots WHERE account=? ORDER BY id DESC LIMIT ?",
            (account, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def record_order(self, client_id: str, order_id: str, market_id: str, side: str,
                     price: float, size: float, filled: float, status: str,
                     raw: dict | None = None) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO orders (client_id, order_id, ts, market_id, side, price, size,"
                " filled, status, raw) VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(client_id) DO UPDATE SET order_id=excluded.order_id,"
                " filled=excluded.filled, status=excluded.status, raw=excluded.raw",
                (client_id, order_id, _now(), market_id, side, price, size, filled, status,
                 json.dumps(raw or {}, default=str)),
            )
            self.conn.commit()

    def snapshot_quote(self, market_id: str, bid: Optional[float], ask: Optional[float]) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO market_snapshots (ts, market_id, bid, ask) VALUES (?,?,?,?)",
                (_now(), market_id, bid, ask),
            )
            self.conn.commit()

    def set_kv(self, key: str, value: Any) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO kv (key, value) VALUES (?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, default=str)),
            )
            self.conn.commit()

    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    # --- reads -----------------------------------------------------------
    def open_bets(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM bets WHERE outcome IS NULL"
            " AND COALESCE(status, 'open') != 'closed'").fetchall()
        return [dict(r) for r in rows]

    def settled_bets(self, limit: int = 1000,
                     mode: str | None = None) -> list[dict]:
        """Bets with realized PnL: resolved (outcome set) or closed early.

        `mode` filters in SQL rather than in the caller — filtering a truncated
        page in Python silently drops the oldest rows of the mode you wanted,
        which for an equity total is a permanent divergence, not a display
        glitch."""
        sql = ("SELECT * FROM bets WHERE (outcome IS NOT NULL"
               " OR COALESCE(status, '') = 'closed')")
        args: list = []
        if mode is not None:
            sql += " AND COALESCE(mode, 'paper') = ?"
            args.append(mode)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def open_bets_for(self, mode: str) -> list[dict]:
        """Open bets for one account's book (see `settled_bets`)."""
        rows = self.conn.execute(
            "SELECT * FROM bets WHERE outcome IS NULL"
            " AND COALESCE(status,'') != 'closed'"
            " AND COALESCE(mode, 'paper') = ?", (mode,)).fetchall()
        return [dict(r) for r in rows]

    def bets_today(self) -> list[dict]:
        today = datetime.now(timezone.utc).date().isoformat()
        rows = self.conn.execute(
            "SELECT * FROM bets WHERE ts >= ?", (today,)
        ).fetchall()
        return [dict(r) for r in rows]

    def last_snapshot(self, market_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM market_snapshots WHERE market_id=? ORDER BY id DESC LIMIT 1",
            (market_id,),
        ).fetchone()
        return dict(row) if row else None

    def exposure_by(self) -> dict:
        """Open (unsettled) cost-basis exposure: total, per sport, per market."""
        rows = self.open_bets()
        total = sum(r["stake"] or 0.0 for r in rows)
        by_sport: dict[str, float] = {}
        by_market: dict[str, float] = {}
        for r in rows:
            by_sport[r["sport"]] = by_sport.get(r["sport"], 0.0) + (r["stake"] or 0.0)
            by_market[r["market_id"]] = by_market.get(r["market_id"], 0.0) + (r["stake"] or 0.0)
        return {"total": total, "by_sport": by_sport, "by_market": by_market,
                "open_positions": len(rows)}
