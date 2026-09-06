"""Mean-reversion generator: fade large short-horizon price moves.

If the mid moved more than `overreaction_threshold` since `lookback_scans` ago, forecast a
partial reversion toward the older price. Markets overreact to single headlines; part of a
sharp move usually decays.
"""
from __future__ import annotations

from ..base import Context, Forecast, SignalGenerator
from ...client import Market


class MeanReversion(SignalGenerator):
    name = "mean_reversion"

    def forecast(self, market: Market, ctx: Context) -> Forecast | None:
        lookback = int(self.cfg.get("lookback_scans", 8))
        threshold = float(self.cfg.get("overreaction_threshold", 0.10))
        factor = float(self.cfg.get("reversion_factor", 0.5))

        history = ctx.price_history.get(market.ticker, [])
        if len(history) < lookback:
            return None
        old_price = history[-lookback][1]
        mid = market.mid
        if not (0 < mid < 1) or not (0 < old_price < 1):
            return None

        move = mid - old_price
        if abs(move) < threshold:
            return None

        target = mid - move * factor
        return Forecast(
            generator=self.name,
            prob_yes=target,
            confidence=self.confidence,
            rationale=f"moved {move:+.2f} over {lookback} scans; fading {factor:.0%} back to {target:.2f}",
        )
