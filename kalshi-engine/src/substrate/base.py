"""Substrate core: SignalGenerator interface and Forecast type."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from ..client import Market


@dataclass
class Context:
    """Shared per-cycle context handed to every generator."""
    # ticker -> list of (iso_ts, mid_price), oldest first, from the DB
    price_history: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    # expected seconds between scans; None disables gap checking (backtests)
    scan_interval_sec: float | None = None


def window_contiguous(window: list[tuple[str, float]],
                      interval_sec: float | None, factor: float = 2.5) -> bool:
    """True when a history window's wall-clock span matches its row count.

    'N scans ago' is only meaningful if the N rows are actually consecutive
    scans — after downtime or a market dropping out of the filter set, Friday's
    price sits one row before Monday's, and a 3-day repricing would read as a
    single-scan 'overreaction'.
    """
    if not interval_sec or len(window) < 2:
        return True
    try:
        t0 = datetime.fromisoformat(window[0][0])
        t1 = datetime.fromisoformat(window[-1][0])
    except (ValueError, TypeError):
        return True
    return (t1 - t0).total_seconds() <= (len(window) - 1) * interval_sec * factor


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
