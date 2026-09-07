"""Line-movement momentum generator: follow persistent drifts.

The complement of mean-reversion: a price that has moved the same direction
across most recent scans (steady drift, not one jump) tends to keep drifting —
information arrives gradually and markets underreact to slow news. A single
large move is mean-reversion's territory and is ignored here.
"""
from __future__ import annotations

from ..base import Context, Forecast, SignalGenerator
from ...client import Market


class Momentum(SignalGenerator):
    name = "momentum"

    def forecast(self, market: Market, ctx: Context) -> Forecast | None:
        lookback = int(self.cfg.get("lookback_scans", 6))
        min_total_move = float(self.cfg.get("min_total_move", 0.03))
        max_step = float(self.cfg.get("max_step", 0.05))         # bigger jumps = news shock, skip
        min_consistency = float(self.cfg.get("min_consistency", 0.7))
        continuation = float(self.cfg.get("continuation_factor", 0.4))

        history = ctx.price_history.get(market.ticker, [])
        if len(history) < lookback:
            return None
        window = [p for _, p in history[-lookback:]] + [market.mid]
        mid = market.mid
        if not (0 < mid < 1):
            return None

        steps = [b - a for a, b in zip(window, window[1:])]
        total = window[-1] - window[0]
        if abs(total) < min_total_move or any(abs(s) > max_step for s in steps):
            return None
        moved = [s for s in steps if s != 0]
        if not moved:
            return None
        # consistency: fraction of non-flat steps agreeing with the overall direction
        agreeing = sum(1 for s in moved if (s > 0) == (total > 0))
        consistency = agreeing / len(moved)
        if consistency < min_consistency:
            return None

        target = mid + total * continuation
        return Forecast(
            generator=self.name,
            prob_yes=target,
            confidence=self.confidence * consistency,
            rationale=(f"drifted {total:+.2f} over {lookback} scans "
                       f"({consistency:.0%} consistent); continuing to {target:.2f}"),
        )
