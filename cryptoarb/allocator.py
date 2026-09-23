"""Capital allocation across strategies.

"Allocate to what appears most profitable so far" — with the honest caveat
that NOTHING here has a demonstrated edge yet. So allocation is driven by
two things, in order:

1. STRUCTURAL RISK PRIOR (fixed, not fitted). How much can go wrong between
   detecting an edge and banking it?
     bundle        — both legs on one venue, settles at exactly $1, no
                     direction, no transfer. Safest structure -> biggest cap.
     triangular    — one venue, no transfers, but 3 legs of execution risk.
     cross_exchange— needs pre-funded inventory on BOTH venues and carries
                     USD/USDT depeg risk. Riskiest structure -> smallest cap.
2. MEASURED EVIDENCE. Realized net PnL per strategy scales its cap between a
   floor and its prior. Consistent with this repo's standing rule
   (CLAUDE.md): losses only ever REDUCE allocation. There is no martingale,
   no doubling down, no "due for a win".

Unallocated capital sits in RESERVE. With no opportunity clearing costs,
100% reserve is the correct answer, not a bug.
"""

from __future__ import annotations

STRUCTURAL_PRIOR = {"bundle": 0.50, "triangular": 0.30, "cross_exchange": 0.20}
FLOOR = 0.25      # a losing strategy keeps at most this share of its prior
EXPLORE_MIN = 5.0  # $ per trade minimum so a fill is measurable


def allocations(bankroll: float, realized: dict) -> dict:
    """{strategy: dollar cap}. `realized` is {strategy: net pnl so far}."""
    out = {}
    for key, prior in STRUCTURAL_PRIOR.items():
        pnl = float(realized.get(key, 0.0) or 0.0)
        if pnl >= 0:
            scale = 1.0
        else:
            # Shrink toward the floor as losses approach 5% of bankroll.
            hurt = min(1.0, abs(pnl) / max(1e-9, 0.05 * bankroll))
            scale = max(FLOOR, 1.0 - hurt * (1.0 - FLOOR))
        out[key] = round(bankroll * prior * scale, 2)
    return out


def size_for(opp, caps: dict, deployed: dict, bankroll: float) -> float:
    """Dollars to commit to one opportunity, under its strategy's cap."""
    cap = caps.get(opp.strategy, 0.0)
    used = deployed.get(opp.strategy, 0.0)
    room = max(0.0, cap - used)
    if room < EXPLORE_MIN:
        return 0.0
    return min(room, opp.max_notional, bankroll)
