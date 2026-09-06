"""Calibration & activity report over the engine's SQLite logs.

Usage:
    python -m src.report            # summary + calibration
    python -m src.report --days 14 # restrict to the last N days

Resolution proxy: a market whose last recorded mid is <= 0.05 or >= 0.95 is treated
as (almost) resolved NO/YES. Forecasts on such markets are scored with the Brier
score against that proxy outcome, per generator, next to the market-implied
baseline — a generator only earns its keep if it beats the baseline.
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from .config import load_config
from .storage.db import Database

RESOLVED_NO, RESOLVED_YES = 0.05, 0.95


def _since_clause(days: float | None) -> tuple[str, tuple]:
    if not days:
        return "", ()
    return " AND ts >= datetime('now', ?)", (f"-{int(days)} days",)


def final_mids(conn) -> dict[str, float]:
    rows = conn.execute(
        "SELECT ticker, mid FROM prices WHERE (ticker, ts) IN"
        " (SELECT ticker, MAX(ts) FROM prices GROUP BY ticker)"
    ).fetchall()
    return {t: m for t, m in rows if m is not None}


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

    finals = final_mids(conn)
    resolved = {
        t: (1.0 if m >= RESOLVED_YES else 0.0)
        for t, m in finals.items()
        if m <= RESOLVED_NO or m >= RESOLVED_YES
    }
    print(f"\nmarkets with a resolution proxy: {len(resolved)} of {len(finals)} tracked")
    if not resolved:
        print("not enough settled history yet — keep the engine scanning")
        return

    # Brier per generator vs proxy outcomes
    stats: dict[str, list[float]] = defaultdict(list)
    for ticker, generator, prob in conn.execute(
        f"SELECT ticker, generator, prob_yes FROM forecasts WHERE 1=1{where}", params
    ).fetchall():
        outcome = resolved.get(ticker)
        if outcome is not None and prob is not None:
            stats[generator].append((prob - outcome) ** 2)

    print("\ncalibration (Brier score, lower is better):")
    for gen, sq in sorted(stats.items(), key=lambda kv: sum(kv[1]) / len(kv[1])):
        print(f"  {gen:<16} n={len(sq):<5} brier={sum(sq) / len(sq):.4f}")

    # hypothetical PnL of dry-run signals against proxy outcomes
    pnl = wins = n = 0.0
    for ticker, side, price, count in conn.execute(
        f"SELECT ticker, side, price, count FROM orders WHERE status='dry_run'{where}", params
    ).fetchall():
        outcome = resolved.get(ticker)
        if outcome is None or price is None or not count:
            continue
        payout = outcome if side == "yes" else 1 - outcome
        pnl += (payout - price) * count
        wins += payout
        n += 1
    if n:
        print(f"\ndry-run signals with proxy outcome: {int(n)}  "
              f"hit-rate={wins / n:.1%}  hypothetical PnL=${pnl:+.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kalshi engine calibration report")
    parser.add_argument("--days", type=float, default=None, help="only the last N days")
    args = parser.parse_args()
    db = Database(load_config().db_path)
    report(db, args.days)


if __name__ == "__main__":
    main()
