"""Time-decay generator: near expiry, push probabilities toward their resolved extreme.

Longshot bias: contracts near expiry tend to be overpriced for the trailing side and
underpriced for the leading side. Nudge the forecast away from 0.5 as expiry approaches.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..base import Context, Forecast, SignalGenerator
from ...client import Market


class TimeDecay(SignalGenerator):
    name = "time_decay"

    def forecast(self, market: Market, ctx: Context) -> Forecast | None:
        horizon_hours = float(self.cfg.get("horizon_hours", 48))
        strength = float(self.cfg.get("drift_strength", 0.15))

        if market.expiration is None:
            return None
        hours_left = (market.expiration - datetime.now(timezone.utc)).total_seconds() / 3600
        if hours_left <= 0 or hours_left > horizon_hours:
            return None

        mid = market.mid
        if not (0 < mid < 1) or abs(mid - 0.5) < 0.05:
            return None  # no lean to amplify

        # urgency 0→1 as expiry approaches; drift toward 1 if leading, 0 if trailing
        urgency = 1 - hours_left / horizon_hours
        extreme = 1.0 if mid > 0.5 else 0.0
        target = mid + (extreme - mid) * strength * urgency
        return Forecast(
            generator=self.name,
            prob_yes=target,
            confidence=self.confidence * urgency,
            rationale=f"{hours_left:.0f}h left; drifting {mid:.2f}→{target:.2f}",
        )
