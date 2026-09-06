"""Substrate milestone 1: adapter from sportsbot's logs to the substrate
ingest.py schema:

    event_id,domain,close_time,resolve_time,market_prob,baseline_prob,outcome

Mapping (all decision-time, never backfilled):
    market_prob   — the market snapshot mid recorded in the same cycle the
                    prediction was made (predictions and snapshots are written
                    together by the runner).
    baseline_prob — the bot model's own probability (prob_raw before market
                    blending): exactly the "conventional baseline" expert the
                    substrate protocol expects.
    outcome       — venue resolution (fetched on demand for markets the bot
                    never bet); blank while unresolved.

The substrate engine then scores its nulls (coin / longshot-corrected market
/ bot baseline) and any sealed channel calls against these events.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from typing import Optional

from sportsbot.data.store import Store
from sportsbot.exchanges.base import ExchangeClient

log = logging.getLogger(__name__)


def _epoch(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return 0.0


def export_events_csv(
    store: Store,
    out_path: str,
    data_client: Optional[ExchangeClient] = None,
    domain: str = "sports",
    max_resolution_calls: int = 300,
) -> dict:
    """Export one row per predicted market. Returns a summary dict.

    When `data_client` is given, unresolved markets get a bounded number of
    resolution lookups so the export carries outcomes even for markets the
    bot only predicted and never traded.
    """
    rows = store.conn.execute(
        """
        SELECT p.market_id, p.sport, p.ts, p.prob_yes, p.prob_raw,
               (SELECT s.bid FROM market_snapshots s
                WHERE s.market_id = p.market_id AND s.ts <= p.ts
                ORDER BY s.id DESC LIMIT 1) AS bid,
               (SELECT s.ask FROM market_snapshots s
                WHERE s.market_id = p.market_id AND s.ts <= p.ts
                ORDER BY s.id DESC LIMIT 1) AS ask
        FROM predictions p
        WHERE p.id IN (SELECT MAX(id) FROM predictions GROUP BY market_id)
        ORDER BY p.ts
        """
    ).fetchall()

    # Outcomes already known from settled bets (side-normalized to YES).
    outcomes: dict[str, int] = {}
    for b in store.settled_bets(limit=100000):
        yes_won = b["outcome"] if b["side"] == "yes" else 1 - b["outcome"]
        outcomes[b["market_id"]] = int(yes_won)

    resolution_calls = 0
    written = 0
    skipped_no_market_prob = 0
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["event_id", "domain", "close_time", "resolve_time",
                    "market_prob", "baseline_prob", "outcome"])
        for r in rows:
            if r["bid"] is None or r["ask"] is None:
                skipped_no_market_prob += 1
                continue
            market_prob = (r["bid"] + r["ask"]) / 2.0
            baseline = r["prob_raw"] if r["prob_raw"] is not None else r["prob_yes"]
            outcome: Optional[int] = outcomes.get(r["market_id"])
            if outcome is None and data_client is not None \
                    and resolution_calls < max_resolution_calls:
                resolution_calls += 1
                try:
                    res = data_client.get_resolution(r["market_id"])
                except Exception:
                    res = None
                if res is not None:
                    outcome = int(res)
            t = _epoch(r["ts"])
            w.writerow([
                r["market_id"], domain, f"{t:.0f}", f"{t:.0f}",
                f"{market_prob:.4f}", f"{float(baseline):.4f}",
                "" if outcome is None else outcome,
            ])
            written += 1
    summary = {
        "rows": written,
        "with_outcome": sum(1 for r in rows if outcomes.get(r["market_id"]) is not None),
        "resolution_lookups": resolution_calls,
        "skipped_no_quote": skipped_no_market_prob,
        "path": out_path,
    }
    log.info("substrate export: %s", summary)
    return summary
