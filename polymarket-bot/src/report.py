"""Calibration & activity report over the bot's SQLite logs.

Usage:
    python -m src.report            # summary + calibration
    python -m src.report --days 14 # restrict to the last N days

Resolution proxy: a token whose last recorded ask is <= 0.05 or >= 0.95 is treated
as (almost) resolved NO/YES. The model's fair_prob is Brier-scored against that
proxy next to the market's own price — the model only earns its keep if it beats
the market baseline.
"""
from __future__ import annotations

import argparse

from .config import load_config
from .storage.db import Database

RESOLVED_NO, RESOLVED_YES = 0.05, 0.95


def _since_clause(days: float | None) -> tuple[str, tuple]:
    if not days:
        return "", ()
    return " AND ts >= datetime('now', ?)", (f"-{int(days)} days",)


def final_asks(conn) -> dict[str, float]:
    rows = conn.execute(
        "SELECT token_id, ask FROM estimates WHERE (token_id, ts) IN"
        " (SELECT token_id, MAX(ts) FROM estimates GROUP BY token_id)"
    ).fetchall()
    return {t: a for t, a in rows if a is not None}


def report(db: Database, days: float | None) -> None:
    conn = db.conn
    where, params = _since_clause(days)

    n_scans, first, last = conn.execute(
        f"SELECT COUNT(*), MIN(ts), MAX(ts) FROM scans WHERE 1=1{where}", params
    ).fetchone()
    print(f"scans: {n_scans}  ({first} → {last})")

    for status, n, stake in conn.execute(
        f"SELECT status, COUNT(*), COALESCE(SUM(stake_usd),0) FROM orders WHERE 1=1{where}"
        " GROUP BY status ORDER BY 2 DESC", params
    ).fetchall():
        print(f"orders[{status}]: {n}  (${stake:.2f})")

    finals = final_asks(conn)
    resolved = {
        t: (1.0 if a >= RESOLVED_YES else 0.0)
        for t, a in finals.items()
        if a <= RESOLVED_NO or a >= RESOLVED_YES
    }
    print(f"\ntokens with a resolution proxy: {len(resolved)} of {len(finals)} tracked")
    if not resolved:
        print("not enough settled history yet — keep the bot scanning")
        return

    model_sq, market_sq = [], []
    for token_id, fair, ask in conn.execute(
        f"SELECT token_id, fair_prob, ask FROM estimates WHERE 1=1{where}", params
    ).fetchall():
        outcome = resolved.get(token_id)
        if outcome is None or fair is None:
            continue
        model_sq.append((fair - outcome) ** 2)
        if ask is not None:
            market_sq.append((ask - outcome) ** 2)

    print("\ncalibration (Brier score, lower is better):")
    if model_sq:
        print(f"  fair_prob model  n={len(model_sq):<5} brier={sum(model_sq) / len(model_sq):.4f}")
    if market_sq:
        print(f"  market ask       n={len(market_sq):<5} brier={sum(market_sq) / len(market_sq):.4f}")

    # hypothetical PnL of dry-run signals against proxy outcomes
    pnl = wins = n = 0.0
    for token_id, ask, shares in conn.execute(
        f"SELECT token_id, ask, shares FROM orders WHERE status='dry_run'{where}", params
    ).fetchall():
        outcome = resolved.get(token_id)
        if outcome is None or ask is None or not shares:
            continue
        pnl += (outcome - ask) * shares
        wins += outcome
        n += 1
    if n:
        print(f"\ndry-run signals with proxy outcome: {int(n)}  "
              f"hit-rate={wins / n:.1%}  hypothetical PnL=${pnl:+.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket bot calibration report")
    parser.add_argument("--days", type=float, default=None, help="only the last N days")
    args = parser.parse_args()
    db = Database(load_config().db_path)
    report(db, args.days)


if __name__ == "__main__":
    main()
