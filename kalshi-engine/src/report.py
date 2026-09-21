"""Calibration & activity report over the engine's SQLite logs.

Usage:
    python -m src.report            # summary + calibration
    python -m src.report --days 14 # restrict to the last N days

Outcomes: real settlements are fetched from the Kalshi API (public, no auth) for
every ticker we ever forecast, and cached in the settlements table. Markets not
yet settled fall back to a resolution proxy — last recorded mid <= 0.05 or
>= 0.95 counts as (almost) resolved NO/YES. Forecasts are Brier-scored against
those outcomes per generator, next to the market-implied baseline — a generator
only earns its keep if it beats the baseline.
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from .client import KalshiClient
from .config import load_config
from .storage.db import Database

SETTLED_STATUSES = {"settled", "finalized"}

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


def resolve_settlements(db: Database, client: KalshiClient) -> None:
    """Look up real results for forecast tickers we haven't settled yet."""
    pending = db.unsettled_forecast_tickers()
    if not pending:
        return
    results = {
        m.ticker: m.result
        for m in client.markets_by_tickers(pending)
        if m.status in SETTLED_STATUSES and m.result in ("yes", "no")
    }
    if results:
        db.record_settlements(results)
    print(f"settlements: {len(results)} newly resolved, {len(pending) - len(results)} still open")


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
    real = db.settled_outcomes()
    n_proxy_only = len(set(resolved) - set(real))
    resolved.update(real)  # real settlements override the price proxy
    print(f"\noutcomes: {len(real)} settled, {n_proxy_only} proxy-only, "
          f"{len(finals)} markets tracked")
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
    parser.add_argument("--no-resolve", action="store_true",
                        help="skip fetching settlements from the API (offline)")
    args = parser.parse_args()
    cfg = load_config()
    db = Database(cfg.db_path)
    if not args.no_resolve:
        try:
            resolve_settlements(db, KalshiClient(demo=cfg.use_demo, read_prod=cfg.read_prod))
        except Exception as e:  # noqa: BLE001 — a network hiccup shouldn't kill the report
            print(f"settlement lookup failed ({e}); reporting with cached outcomes")
    report(db, args.days)


if __name__ == "__main__":
    main()
