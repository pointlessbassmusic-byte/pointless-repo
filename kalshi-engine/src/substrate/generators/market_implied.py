"""Baseline generator: the market's own mid price, as an anchor for the ensemble."""
from __future__ import annotations

from ..base import Context, Forecast, SignalGenerator
from ...client import Market


class MarketImplied(SignalGenerator):
    name = "market_implied"

    def forecast(self, market: Market, ctx: Context) -> Forecast | None:
        mid = market.mid
        if not (0 < mid < 1):
            return None
        return Forecast(
            generator=self.name,
            prob_yes=mid,
            confidence=self.confidence,
            rationale=f"mid {mid:.2f} (bid {market.yes_bid:.2f}/ask {market.yes_ask:.2f})",
        )
