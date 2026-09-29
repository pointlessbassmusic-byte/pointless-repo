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
from typing import Optional
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
    # In-play detector, independent of venue timestamps: refuse an entry
    # once the mid has moved this far from the first mid the bot recorded
    # for the market. Kalshi's occurrence_datetime turned out to be the
    # expected END on tennis, so a clock-based guard alone can pass a live
    # match; a pre-match line rarely moves 8c (MLB mean move 24h->3h was
    # 2.2c), while a live one moves that in minutes.
    max_pre_match_move: float = 0.08
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
                (r.get("pnl") or 0.0) for r in today if r.get("pnl") is not None
            )
            if realized_today <= -abs(self.cfg.daily_loss_limit):
                return False, f"daily loss limit ({realized_today:.2f})"

            staked_today = sum(r.get("stake") or 0.0 for r in today)
            if staked_today >= self.cfg.max_daily_new_risk * self.cfg.bankroll:
                return False, f"daily new-risk cap ({staked_today:.2f})"

            # Early-closed bets carry pnl but no outcome — they belong in the
            # drawdown/daily-loss sums above but not in calibration scoring.
            recent = [r for r in settled[: 200]
                      if r.get("model_prob") is not None
                      and r.get("outcome") is not None]
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
    def _pre_match_move(self, market_id: str, quote: MarketQuote) -> Optional[float]:
        """|mid now - mid when first seen|, persisted so a restart does not
        forget where a market opened. None when there is no two-sided book
        to measure against; the caller treats that as no evidence, and the
        book check upstream already refuses one-sided markets."""
        if quote.bid is None or quote.ask is None:
            return None
        mid = (quote.bid + quote.ask) / 2.0
        key = f"first_mid:{market_id}"
        first = self.store.get_kv(key)
        if not isinstance(first, dict) or "mid" not in first:
            self.store.set_kv(key, {"mid": mid, "ts": _now_iso()})
            return 0.0
        return abs(mid - float(first["mid"]))

    def check_intent(self, intent: BetIntent, quote: MarketQuote) -> tuple[bool, str]:
        try:
            age = (datetime.now(timezone.utc) - quote.ts).total_seconds()
            if age > self.cfg.stale_quote_seconds:
                return False, f"stale quote ({age:.0f}s)"
            # No fallback to close_time: on Kalshi it is the expected SETTLE
            # bound, so it would pass a match that is already live. An
            # unknown start is refused, not guessed.
            start = intent.market.start_time
            if start is None:
                return False, "start time unknown — refusing rather than risk in-play"
            mins = (start - datetime.now(timezone.utc)).total_seconds() / 60.0
            if mins < self.cfg.min_minutes_before_start:
                return False, f"too close to start ({mins:.0f}m)"
            moved = self._pre_match_move(intent.market.market_id, quote)
            if moved is not None and moved > self.cfg.max_pre_match_move:
                return False, (f"price moved {moved:.2f} since first seen "
                               f"(> {self.cfg.max_pre_match_move:.2f}) — possibly in play")
            if not (0.0 < intent.price < 1.0) or intent.size <= 0:
                return False, "malformed intent"
            return True, "ok"
        except Exception as exc:  # fail closed
            log.exception("intent check error")
            return False, f"intent check error: {exc}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
