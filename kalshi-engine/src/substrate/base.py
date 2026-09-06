"""Substrate core: SignalGenerator interface and Forecast type."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..client import Market


@dataclass
class Context:
    """Shared per-cycle context handed to every generator."""
    # ticker -> list of (iso_ts, mid_price), oldest first, from the DB
    price_history: dict[str, list[tuple[str, float]]] = field(default_factory=dict)


@dataclass
class Forecast:
    generator: str
    prob_yes: float      # 0-1
    confidence: float    # 0-1 ensemble weight
    rationale: str = ""

    def clamped(self) -> "Forecast":
        self.prob_yes = min(0.99, max(0.01, self.prob_yes))
        self.confidence = min(1.0, max(0.0, self.confidence))
        return self


class SignalGenerator(ABC):
    """One pluggable prediction source. Return None for markets you have no view on."""

    name: str = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.confidence = float(cfg.get("confidence", 0.3))

    @abstractmethod
    def forecast(self, market: Market, ctx: Context) -> Forecast | None:
        ...
