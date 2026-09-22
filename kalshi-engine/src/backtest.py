"""Replay backtester: re-run the substrate over recorded price history.

For every ticker in the prices table, walk its history chronologically and at
each step hand the generators only the history up to that point — exactly what
they would have seen live. Each forecast is scored against the market's real
outcome (settlements table, falling back to a >=0.95/<=0.05 final-price proxy)
and against what the market itself said at forecast time (the mid), so every
generator is judged on whether it beat the market from the same information.

Usage:
    python -m src.backtest              # all recorded history
    python -m src.backtest --min-scans 8

This is offline tuning machinery: edit generator parameters in config.yaml and
re-run — no API calls, no waiting for new scans.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass

from .client import Market
from .config import load_config
from .main import build_ensemble
from .report import RESOLVED_NO, RESOLVED_YES
from .storage.db import Database
from .substrate.base import Context


@dataclass
class Score:
    n: int = 0
    brier_sum: float = 0.0

    def add(self, prob: float, outcome: float) -> None:
        self.n += 1
        self.brier_sum += (prob - outcome) ** 2

    @property
    def brier(self) -> float:
        return self.brier_sum / self.n if self.n else float("nan")


def outcomes_for(db: Database) -> dict[str, float]:
    """Real settlements first, final-price proxy for the rest."""
    finals = db.conn.execute(
        "SELECT ticker, mid FROM prices WHERE (ticker, ts) IN"
        " (SELECT ticker, MAX(ts) FROM prices GROUP BY ticker)"
    ).fetchall()
    out = {
        t: (1.0 if m >= RESOLVED_YES else 0.0)
        for t, m in finals
        if m is not None and (m <= RESOLVED_NO or m >= RESOLVED_YES)
    }
    out.update(db.settled_outcomes())
    return out


def replay(db: Database, ensemble, min_scans: int = 4) -> dict[str, Score]:
    outcomes = outcomes_for(db)
    rows = db.conn.execute(
        "SELECT ticker, ts, mid FROM prices ORDER BY ticker, ts"
    ).fetchall()
    by_ticker: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for ticker, ts, mid in rows:
        if mid is not None:
            by_ticker[ticker].append((ts, mid))

    scores: dict[str, Score] = defaultdict(Score)
    n_forecast_points = 0
    for ticker, history in by_ticker.items():
        outcome = outcomes.get(ticker)
        if outcome is None or len(history) < min_scans:
            continue
        # the last point is the outcome proxy itself for unsettled markets —
        # never let a generator be scored on the price it is predicting
        for i in range(min_scans, len(history) - 1):
            ts, mid = history[i]
            if not (0 < mid < 1):
                continue
            market = Market(ticker=ticker, event_ticker="", title="",
                            yes_bid=mid, yes_ask=mid, last_price=mid,
                            volume=0, open_interest=0, expiration=None, status="active")
            ctx = Context(price_history={ticker: history[:i]})
            res = ensemble.predict(market, ctx)
            if res is None:
                continue
            n_forecast_points += 1
            scores["ensemble"].add(res.prob_yes, outcome)
            scores["market (baseline)"].add(mid, outcome)
            for f in res.forecasts:
                scores[f.generator].add(f.prob_yes, outcome)

    print(f"replayed {n_forecast_points} forecast points over "
          f"{len(by_ticker)} tickers ({len(outcomes)} with outcomes)")
    return scores


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay recorded history through the substrate")
    parser.add_argument("--min-scans", type=int, default=4,
                        help="minimum history points before forecasting a ticker")
    args = parser.parse_args()

    cfg = load_config()
    db = Database(cfg.db_path)
    ensemble = build_ensemble(cfg.substrate)
    scores = replay(db, ensemble, min_scans=args.min_scans)

    if not scores:
        print("no scoreable history yet — run more scan cycles first")
        return
    baseline = scores.get("market (baseline)")
    print("\nBrier by source (lower is better; beat the baseline or retire the generator):")
    for name, sc in sorted(scores.items(), key=lambda kv: kv[1].brier):
        marker = ""
        if baseline and baseline.n and name not in ("market (baseline)",):
            marker = "  << beats market" if sc.brier < baseline.brier else ""
        print(f"  {name:<18} n={sc.n:<6} brier={sc.brier:.4f}{marker}")


if __name__ == "__main__":
    main()
