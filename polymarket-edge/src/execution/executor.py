"""Order execution: dry-run logging by default, live CLOB limit orders when enabled."""
from __future__ import annotations

import logging

from ..clients.clob import ClobClient
from ..storage.db import Database
from ..strategy.edge import TradeSignal

log = logging.getLogger(__name__)


class Executor:
    def __init__(self, clob: ClobClient, db: Database, live: bool, risk_gate=None):
        self.clob = clob
        self.db = db
        self.live = live
        self.risk_gate = risk_gate

    def execute(self, signals: list[TradeSignal]) -> None:
        blocked_reason = ""
        if self.live and self.risk_gate is not None:
            ok, reason = self.risk_gate.check()
            if not ok:
                blocked_reason = reason
                log.warning("RISK GATE BLOCKED live orders: %s", reason)
        for s in signals:
            if blocked_reason:
                self.db.record_order(s, f"blocked:{blocked_reason}")
                continue
            if self.live:
                try:
                    resp = self.clob.place_limit_buy(s.token_id, s.ask, s.shares)
                    status = "placed" if resp.get("success") else f"rejected:{resp.get('errorMsg')}"
                except Exception as e:  # noqa: BLE001 — record any order failure, keep going
                    log.exception("order failed for %s", s.market_question)
                    status = f"error:{e}"
            else:
                status = "dry_run"
                log.info(
                    "DRY RUN: buy %.2f sh of '%s' [%s] @ %.3f (fair %.3f, edge %.3f, $%.2f) — %s",
                    s.shares, s.market_question, s.outcome_name, s.ask,
                    s.fair_prob, s.edge, s.stake_usd, s.matched_game,
                )
            self.db.record_order(s, status)
