"""Calibration & activity report over the bot's SQLite logs.

Usage:
    python -m src.report            # summary + calibration
    python -m src.report --days 14 # restrict to the last N days

Outcomes: real resolutions are fetched from the Gamma API (no auth) for every
market we ever estimated, and cached in the settlements table. Tokens not yet
resolved fall back to a proxy — last recorded ask <= 0.05 or >= 0.95 counts as
(almost) resolved NO/YES. The model's fair_prob is Brier-scored against those
outcomes next to the market's own price — the model only earns its keep if it
beats the market baseline.
"""
from __future__ import annotations

import argparse

from .clients.gamma import GammaClient
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


def resolve_settlements(db: Database, gamma: GammaClient) -> None:
    """Look up real resolutions for estimated markets we haven't settled yet."""
    pending = db.unsettled_condition_ids()
    if not pending:
        return
    outcomes = gamma.resolutions(pending)
    if outcomes:
        db.record_settlements(outcomes)
    print(f"settlements: {len(outcomes)} tokens newly resolved "
          f"across {len(pending)} unresolved markets")


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
    real = db.settled_outcomes()
    n_proxy_only = len(set(resolved) - set(real))
    resolved.update(real)  # real resolutions override the price proxy
    print(f"\noutcomes: {len(real)} resolved, {n_proxy_only} proxy-only, "
          f"{len(finals)} tokens tracked")
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
    parser.add_argument("--no-resolve", action="store_true",
                        help="skip fetching resolutions from the API (offline)")
    args = parser.parse_args()
    db = Database(load_config().db_path)
    if not args.no_resolve:
        try:
            resolve_settlements(db, GammaClient())
        except Exception as e:  # noqa: BLE001 — a network hiccup shouldn't kill the report
            print(f"resolution lookup failed ({e}); reporting with cached outcomes")
    report(db, args.days)


if __name__ == "__main__":
    main()
