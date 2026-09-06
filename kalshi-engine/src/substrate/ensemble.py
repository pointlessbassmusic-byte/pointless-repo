"""Confidence-weighted ensemble over generator forecasts."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..client import Market
from .base import Context, Forecast, SignalGenerator

log = logging.getLogger(__name__)


@dataclass
class EnsembleResult:
    market: Market
    prob_yes: float
    total_confidence: float
    forecasts: list[Forecast]


class Ensemble:
    def __init__(self, generators: list[SignalGenerator]):
        self.generators = generators

    def predict(self, market: Market, ctx: Context) -> EnsembleResult | None:
        forecasts: list[Forecast] = []
        for gen in self.generators:
            try:
                f = gen.forecast(market, ctx)
            except Exception:  # noqa: BLE001 — one generator crashing shouldn't sink the ensemble
                log.exception("generator %s failed on %s", gen.name, market.ticker)
                continue
            if f is not None:
                forecasts.append(f.clamped())

        total_w = sum(f.confidence for f in forecasts)
        if total_w <= 0:
            return None
        prob = sum(f.prob_yes * f.confidence for f in forecasts) / total_w
        return EnsembleResult(market=market, prob_yes=prob, total_confidence=total_w, forecasts=forecasts)
