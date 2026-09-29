"""Fee schedules — the single most important input in this system.

Measured 2026-09-22 on live books: cross-exchange spot spreads on
BTC/ETH/SOL ran 0.0-3.4 bps gross while retail round-trip taker fees are
~50 bps, and 0/16 KuCoin triangular cycles cleared their 30 bps hurdle.
Arbitrage here is decided by YOUR fee tier, not by finding a clever pair —
so the tier is a first-class, user-supplied input, and every opportunity is
reported net of it.

Defaults are the WORST (base retail) tier: a bot that assumes cheap fees
manufactures profits that do not exist. Override with your real tier once
you have an account; better tiers are what make marginal cycles viable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Base (VIP-0 / lowest-volume) published rates, as fractions.
DEFAULT_TAKER = {"coinbase": 0.0060, "kraken": 0.0040, "kucoin": 0.0010,
                 "polymarket": 0.0000}
DEFAULT_MAKER = {"coinbase": 0.0040, "kraken": 0.0025, "kucoin": 0.0010,
                 "polymarket": 0.0000}


@dataclass
class FeeBook:
    """Per-venue fee rates. Everything the engine prices goes through here."""

    taker: dict = field(default_factory=lambda: dict(DEFAULT_TAKER))
    maker: dict = field(default_factory=lambda: dict(DEFAULT_MAKER))

    def taker_rate(self, venue: str) -> float:
        if venue not in self.taker:
            # An unknown venue must never price as free.
            return max(DEFAULT_TAKER.values())
        return self.taker[venue]

    def maker_rate(self, venue: str) -> float:
        if venue not in self.maker:
            return max(DEFAULT_MAKER.values())
        return self.maker[venue]

    def round_trip_bps(self, buy_venue: str, sell_venue: str) -> float:
        """The hurdle a two-leg taker arb must clear, in basis points."""
        return (self.taker_rate(buy_venue) + self.taker_rate(sell_venue)) * 10_000

    @classmethod
    def from_config(cls, cfg: dict) -> "FeeBook":
        fees = (cfg or {}).get("fees", {}) or {}
        taker = dict(DEFAULT_TAKER)
        maker = dict(DEFAULT_MAKER)
        taker.update({k: float(v) for k, v in (fees.get("taker") or {}).items()})
        maker.update({k: float(v) for k, v in (fees.get("maker") or {}).items()})
        return cls(taker=taker, maker=maker)
