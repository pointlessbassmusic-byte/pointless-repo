"""Model-quality accounting: scoring rules, calibration, and closing-line
value. If these numbers go bad, the bot should stop betting — the risk layer
reads them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional


def brier_score(probs: Iterable[float], outcomes: Iterable[int]) -> float:
    pairs = list(zip(probs, outcomes))
    if not pairs:
        raise ValueError("empty inputs")
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


def log_loss(probs: Iterable[float], outcomes: Iterable[int], eps: float = 1e-12) -> float:
    pairs = list(zip(probs, outcomes))
    if not pairs:
        raise ValueError("empty inputs")
    total = 0.0
    for p, o in pairs:
        p = min(1.0 - eps, max(eps, p))
        total += -(o * math.log(p) + (1 - o) * math.log(1.0 - p))
    return total / len(pairs)


def calibration_bins(
    probs: list[float], outcomes: list[int], n_bins: int = 10
) -> list[dict]:
    """Reliability-diagram data: per-bin mean predicted vs. observed frequency."""
    bins: list[dict] = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        members = [
            (p, o) for p, o in zip(probs, outcomes)
            if (lo <= p < hi) or (i == n_bins - 1 and p == hi)
        ]
        if members:
            bins.append({
                "lo": lo,
                "hi": hi,
                "n": len(members),
                "mean_pred": sum(p for p, _ in members) / len(members),
                "observed": sum(o for _, o in members) / len(members),
            })
    return bins


@dataclass
class BetRecord:
    market_id: str
    side: str
    model_prob: float          # our prob for the side bought
    entry_price: float
    stake: float
    closing_price: Optional[float] = None   # market price just before start
    outcome: Optional[int] = None           # 1 = side won, 0 = lost
    pnl: Optional[float] = None

    @property
    def clv(self) -> Optional[float]:
        """Closing-line value: entry vs. close, in probability points.

        Positive = we beat the close. Persistent positive CLV is the
        strongest early evidence of real edge, long before PnL converges.
        """
        if self.closing_price is None:
            return None
        return self.closing_price - self.entry_price


@dataclass
class PerformanceTracker:
    records: list[BetRecord] = field(default_factory=list)

    def add(self, rec: BetRecord) -> None:
        self.records.append(rec)

    def settled(self) -> list[BetRecord]:
        return [r for r in self.records if r.outcome is not None]

    def summary(self) -> dict:
        settled = self.settled()
        with_clv = [r for r in self.records if r.clv is not None]
        out: dict = {
            "n_bets": len(self.records),
            "n_settled": len(settled),
            "total_staked": round(sum(r.stake for r in self.records), 2),
        }
        if settled:
            probs = [r.model_prob for r in settled]
            outcomes = [r.outcome for r in settled]
            pnl = sum(r.pnl or 0.0 for r in settled)
            out.update({
                "pnl": round(pnl, 2),
                "roi": round(pnl / max(1e-9, sum(r.stake for r in settled)), 4),
                "hit_rate": round(sum(outcomes) / len(outcomes), 4),
                "brier": round(brier_score(probs, outcomes), 4),
                "log_loss": round(log_loss(probs, outcomes), 4),
            })
        if with_clv:
            out["mean_clv"] = round(sum(r.clv for r in with_clv) / len(with_clv), 4)
            out["clv_positive_rate"] = round(
                sum(1 for r in with_clv if r.clv > 0) / len(with_clv), 4
            )
        return out

    def drawdown(self) -> float:
        """Max peak-to-trough drawdown of cumulative settled PnL, in dollars."""
        peak = 0.0
        cum = 0.0
        max_dd = 0.0
        for r in self.settled():
            cum += r.pnl or 0.0
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
        return round(max_dd, 2)


def blend_with_market(model_prob: float, market_prob: Optional[float], weight: float = 0.7) -> float:
    """Shrink the model toward the market: q' = w*model + (1-w)*market.

    Markets are hard to beat; treating the market price as a prior reduces
    the damage of model error. weight=1 trusts the model fully.
    """
    if market_prob is None:
        return model_prob
    if not (0.0 <= weight <= 1.0):
        raise ValueError("weight must be in [0,1]")
    return weight * model_prob + (1.0 - weight) * market_prob
