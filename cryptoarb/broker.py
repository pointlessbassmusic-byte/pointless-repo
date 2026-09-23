"""Paper broker. Fills ONLY against observed top-of-book, always pays fees.

The failure mode this guards against is the one this repo already lived
through (archive/README.md: "earlier V2 dashboards overstated performance —
BOOTSTRAP_TRADES + zero fees"). So: no seeded trades, no assumed fills, no
fee-free legs, and size is capped by the depth actually quoted.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Fill:
    ts: str
    strategy: str
    label: str
    notional: float
    gross_bps: float
    fee_bps: float
    net_bps: float
    pnl: float


@dataclass
class PaperBroker:
    starting_cash: float = 100.0
    cash: float = 100.0
    fills: list = field(default_factory=list)

    @property
    def equity(self) -> float:
        return self.cash

    @property
    def realized_pnl(self) -> float:
        return self.cash - self.starting_cash

    def execute(self, opp, notional: float, ts: str):
        """Take an opportunity at the size allowed. Returns a Fill or None.

        Arb legs open and close within the same opportunity (cross-exchange
        and triangular settle instantly; a bundle settles at $1 at
        resolution), so PnL is realized as net_bps on the notional. A
        negative-edge opportunity is never executed — that is the whole
        point of the system.
        """
        if not opp.tradeable:
            return None
        size = min(notional, opp.max_notional, self.cash)
        if size <= 0:
            return None
        pnl = size * (opp.net_bps / 10_000.0)
        self.cash += pnl
        f = Fill(ts=ts, strategy=opp.strategy, label=opp.label, notional=size,
                 gross_bps=opp.gross_bps, fee_bps=opp.fee_bps,
                 net_bps=opp.net_bps, pnl=round(pnl, 6))
        self.fills.append(f)
        return f
