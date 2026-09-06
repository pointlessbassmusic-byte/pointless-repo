"""Order execution: dry-run logging by default, live Kalshi limit orders when enabled."""
from __future__ import annotations

import logging

from ..client import KalshiClient
from ..storage.db import Database
from ..strategy.edge import TradeSignal

log = logging.getLogger(__name__)


class Executor:
    def __init__(self, client: KalshiClient, db: Database, live: bool):
        self.client = client
        self.db = db
        self.live = live

    def execute(self, signals: list[TradeSignal]) -> None:
        for s in signals:
            if self.live:
                try:
                    resp = self.client.place_limit_order(s.ticker, s.side, s.price, s.count)
                    status = f"placed:{resp.get('order', {}).get('order_id', '?')}"
                except Exception as e:  # noqa: BLE001 — record any order failure, keep going
                    log.exception("order failed for %s", s.ticker)
                    status = f"error:{e}"
            else:
                status = "dry_run"
                log.info(
                    "DRY RUN: buy %d x %s [%s] @ %.2f (fair %.2f, edge %.2f, $%.2f) — %s | %s",
                    s.count, s.ticker, s.side.upper(), s.price,
                    s.fair_prob, s.edge, s.stake_usd, s.title, s.rationale,
                )
            self.db.record_order(s, status)
