"""Risk gate: the last check before any order leaves the process, and the
component that can stop the whole bot. Fails CLOSED — any internal error in a
check vetoes the trade.

Controls (defaults from config):
* live-mode double opt-in (config mode=live AND env SPORTSBOT_LIVE=1)
* daily realized-loss limit and daily new-risk cap
* max-drawdown kill switch (manual reset via CLI)
* stale-quote guard
* pre-match cutoff (no entries when the event is about to start / started)
* rolling calibration monitor (pause when Brier decays past threshold)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from sportsbot.core.calibration import brier_score
from sportsbot.core.types import BetIntent, MarketQuote
from sportsbot.data.store import Store

log = logging.getLogger(__name__)

KILL_SWITCH_KEY = "kill_switch_tripped"


@dataclass
class RiskConfig:
    daily_loss_limit: float = 100.0
    max_daily_new_risk: float = 0.25       # fraction of bankroll
    max_drawdown: float = 250.0
    stale_quote_seconds: float = 120.0
    min_minutes_before_start: float = 10.0
    calibration_min_bets: int = 50
    calibration_max_brier: float = 0.26
    bankroll: float = 1000.0


class RiskManager:
    def __init__(self, cfg: RiskConfig, store: Store, mode: str = "paper") -> None:
        self.cfg = cfg
        self.store = store
        self.mode = mode

    # ------------------------------------------------------------------
    def live_allowed(self) -> bool:
        return self.mode == "live" and os.environ.get("SPORTSBOT_LIVE") == "1"

    def kill_switch_tripped(self) -> bool:
        return bool(self.store.get_kv(KILL_SWITCH_KEY, False))

    def trip_kill_switch(self, reason: str) -> None:
        log.critical("KILL SWITCH TRIPPED: %s", reason)
        self.store.set_kv(KILL_SWITCH_KEY, {"reason": reason, "at": _now_iso()})

    def reset_kill_switch(self) -> None:
        self.store.set_kv(KILL_SWITCH_KEY, False)

    # ------------------------------------------------------------------
    def check_global(self) -> tuple[bool, str]:
        """Can the bot bet at all right now?"""
        try:
            if self.kill_switch_tripped():
                return False, f"kill switch: {self.store.get_kv(KILL_SWITCH_KEY)}"

            settled = self.store.settled_bets()
            cum = 0.0
            peak = 0.0
            drawdown = 0.0
            for r in reversed(settled):  # oldest first
                cum += r.get("pnl") or 0.0
                peak = max(peak, cum)
                drawdown = max(drawdown, peak - cum)
            if drawdown >= self.cfg.max_drawdown:
                self.trip_kill_switch(f"max drawdown {drawdown:.2f} >= {self.cfg.max_drawdown}")
                return False, "max drawdown"

            today = self.store.bets_today()
            realized_today = sum(
                (r.get("pnl") or 0.0) for r in today if r.get("outcome") is not None
            )
            if realized_today <= -abs(self.cfg.daily_loss_limit):
                return False, f"daily loss limit ({realized_today:.2f})"

            staked_today = sum(r.get("stake") or 0.0 for r in today)
            if staked_today >= self.cfg.max_daily_new_risk * self.cfg.bankroll:
                return False, f"daily new-risk cap ({staked_today:.2f})"

            recent = [r for r in settled[: 200] if r.get("model_prob") is not None]
            if len(recent) >= self.cfg.calibration_min_bets:
                b = brier_score(
                    [r["model_prob"] for r in recent],
                    [r["outcome"] for r in recent],
                )
                if b > self.cfg.calibration_max_brier:
                    return False, f"calibration decay: rolling Brier {b:.3f}"
            return True, "ok"
        except Exception as exc:  # fail closed
            log.exception("risk check error")
            return False, f"risk check error: {exc}"

    # ------------------------------------------------------------------
    def check_intent(self, intent: BetIntent, quote: MarketQuote) -> tuple[bool, str]:
        try:
            age = (datetime.now(timezone.utc) - quote.ts).total_seconds()
            if age > self.cfg.stale_quote_seconds:
                return False, f"stale quote ({age:.0f}s)"
            start = intent.market.start_time
            if start is not None:
                mins = (start - datetime.now(timezone.utc)).total_seconds() / 60.0
                if mins < self.cfg.min_minutes_before_start:
                    return False, f"too close to start ({mins:.0f}m)"
            if not (0.0 < intent.price < 1.0) or intent.size <= 0:
                return False, "malformed intent"
            return True, "ok"
        except Exception as exc:  # fail closed
            log.exception("intent check error")
            return False, f"intent check error: {exc}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
