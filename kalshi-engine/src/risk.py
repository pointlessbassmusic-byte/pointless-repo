"""Live-trading risk gate: kill switch + daily realized-loss circuit breaker.

Concepts adapted from tradermonty/claude-trading-skills' drawdown-circuit-breaker
(MIT) and ImMike/polymarket-arbitrage's RiskManager; implementation is original.

The gate only guards LIVE order placement — dry runs always proceed so the
signal log keeps accumulating.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .storage.db import Database

log = logging.getLogger(__name__)


class RiskGate:
    def __init__(self, db: Database, cfg: dict, root: Path):
        self.db = db
        self.max_daily_loss = float(cfg.get("max_daily_loss_usd", 50))
        self.kill_switch = root / str(cfg.get("kill_switch_file", "data/KILL_SWITCH"))

    def check(self) -> tuple[bool, str]:
        """(allowed, reason-if-blocked). Touch the kill-switch file to halt trading."""
        if self.kill_switch.exists():
            return False, f"kill switch engaged ({self.kill_switch})"
        pnl = self.db.realized_pnl_today()
        if pnl <= -self.max_daily_loss:
            return False, f"daily loss limit hit (realized {pnl:+.2f} USD today)"
        return True, ""
