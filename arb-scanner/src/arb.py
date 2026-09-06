"""Arbitrage detection with realistic fee accounting.

Two structures on binary markets:

1. Cross-platform complement: buy YES on one platform at `yes_ask` and NO on the
   other at `no_ask`. Exactly one leg pays $1 at resolution, so profit per
   contract-pair = 1 - (yes_ask + no_ask) - fees, locked in at entry — IF the
   two markets resolve identically (matching risk is the real risk).

2. Same-platform bundle: yes_ask + no_ask < 1 - fees on a single market.
   No matching risk; rarer and smaller.

Fees: Kalshi charges roughly fee_rate * P * (1-P) per contract on taker fills
(0.07 general schedule); Polymarket trading is fee-free but on-chain execution
costs gas, modeled as a flat per-trade amount amortized by trade size.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .feeds import BinaryMarket
from .matcher import MarketPair

log = logging.getLogger(__name__)


@dataclass
class Opportunity:
    kind: str                # "cross_platform" | "bundle"
    description: str
    yes_platform: str        # where YES is bought
    no_platform: str         # where NO is bought
    yes_market_id: str
    no_market_id: str
    yes_ask: float
    no_ask: float
    gross_edge: float        # 1 - yes_ask - no_ask
    net_edge: float          # after fees, per $1 contract pair
    similarity: float        # match confidence (1.0 for bundles)
    detected_at: str = ""

    def __post_init__(self):
        if not self.detected_at:
            self.detected_at = datetime.now(timezone.utc).isoformat()


def kalshi_fee(price: float, fee_rate: float) -> float:
    return fee_rate * price * (1 - price)


class ArbDetector:
    def __init__(self, cfg: dict):
        self.min_net_edge = float(cfg.get("min_net_edge", 0.02))
        # a cross-platform "edge" this large almost always means the two markets
        # are NOT the same question — surface it as a suspect match, not an arb
        self.max_net_edge = float(cfg.get("max_net_edge", 0.15))
        self.kalshi_fee_rate = float(cfg.get("kalshi_fee_rate", 0.07))
        self.poly_fee = float(cfg.get("poly_fee_per_contract", 0.0))

    def _fees(self, platform: str, price: float) -> float:
        if platform == "kalshi":
            return kalshi_fee(price, self.kalshi_fee_rate)
        return self.poly_fee

    def _check(self, kind: str, sim: float, desc: str,
               yes_m: BinaryMarket, no_m: BinaryMarket) -> Opportunity | None:
        if yes_m.yes_ask is None or no_m.no_ask is None:
            return None
        yes_ask, no_ask = yes_m.yes_ask, no_m.no_ask
        gross = 1.0 - yes_ask - no_ask
        net = gross - self._fees(yes_m.platform, yes_ask) - self._fees(no_m.platform, no_ask)
        if net < self.min_net_edge:
            return None
        if kind == "cross_platform" and net > self.max_net_edge:
            kind = "suspect_match"
        return Opportunity(
            kind=kind, description=desc,
            yes_platform=yes_m.platform, no_platform=no_m.platform,
            yes_market_id=yes_m.market_id, no_market_id=no_m.market_id,
            yes_ask=yes_ask, no_ask=no_ask,
            gross_edge=round(gross, 4), net_edge=round(net, 4), similarity=sim,
        )

    def scan_pairs(self, pairs: list[MarketPair]) -> list[Opportunity]:
        out = []
        for pr in pairs:
            desc = f"{pr.poly.question}  <->  {pr.kalshi.question}"
            # direction A: YES on Polymarket, NO on Kalshi
            opp = self._check("cross_platform", pr.similarity, desc, pr.poly, pr.kalshi)
            if opp:
                out.append(opp)
            # direction B: YES on Kalshi, NO on Polymarket
            opp = self._check("cross_platform", pr.similarity, desc, pr.kalshi, pr.poly)
            if opp:
                out.append(opp)
        return out

    def scan_bundles(self, markets: list[BinaryMarket]) -> list[Opportunity]:
        out = []
        for m in markets:
            opp = self._check("bundle", 1.0, m.question, m, m)
            if opp:
                out.append(opp)
        return out
