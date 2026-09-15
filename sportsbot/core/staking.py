"""Kelly-criterion staking for binary markets, with the guardrails that make
Kelly survivable in production: fractional sizing, exposure caps, and minimum
edge thresholds.

Derivation for buying a share at price ``p`` paying 1 if the event happens,
with true probability ``q``: staking fraction ``f`` of bankroll maximizes
``q*ln(1 - f + f/p) + (1-q)*ln(1 - f)`` at

    f* = (q - p) / (1 - p)

(fraction of bankroll spent on cost basis). Symmetric for NO at price 1-p.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sportsbot.core.types import Side


def kelly_binary(prob: float, price: float) -> float:
    """Full-Kelly bankroll fraction for buying at `price` with true prob `prob`.

    Returns 0 when there is no positive edge.
    """
    if not (0.0 < price < 1.0):
        return 0.0
    edge = prob - price
    if edge <= 0.0:
        return 0.0
    return edge / (1.0 - price)


@dataclass
class StakingConfig:
    bankroll: float = 1000.0            # USDC / USD
    kelly_multiplier: float = 0.25      # quarter-Kelly: robust to model error
    min_edge: float = 0.03              # skip bets with < 3% absolute edge
    min_stake: float = 5.0              # below venue minimum → no bet
    max_stake_per_market: float = 50.0  # hard dollar cap per market
    max_fraction_per_market: float = 0.05   # ≤5% of bankroll in one market
    max_fraction_per_sport: float = 0.20    # ≤20% of bankroll per sport
    max_total_exposure: float = 0.50        # ≤50% of bankroll deployed at once
    max_open_positions: int = 20


@dataclass
class StakeDecision:
    stake: float                # dollars to spend (cost basis)
    size: float                 # shares = stake / price
    kelly_fraction: float       # post-multiplier fraction of bankroll
    edge: float
    reasons: list[str] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.stake > 0.0


def decide_stake(
    prob: float,
    price: float,
    cfg: StakingConfig,
    side: Side = Side.YES,
    current_market_exposure: float = 0.0,
    current_sport_exposure: float = 0.0,
    current_total_exposure: float = 0.0,
    open_positions: int = 0,
) -> StakeDecision:
    """Size a bet under all configured limits.

    `prob` and `price` are for the side being bought (already flipped for NO
    by the caller: prob_no = 1 - prob_yes, price_no = NO ask).
    Exposure arguments are dollars currently at risk (cost basis).
    """
    reasons: list[str] = []
    edge = prob - price

    if edge < cfg.min_edge:
        return StakeDecision(0.0, 0.0, 0.0, edge, [f"edge {edge:.3f} < min {cfg.min_edge}"])
    if open_positions >= cfg.max_open_positions:
        return StakeDecision(0.0, 0.0, 0.0, edge, ["max open positions reached"])

    f_full = kelly_binary(prob, price)
    f = f_full * cfg.kelly_multiplier
    stake = f * cfg.bankroll

    # Apply caps, tightest wins.
    cap_market = cfg.max_stake_per_market - current_market_exposure
    cap_market = min(cap_market, cfg.max_fraction_per_market * cfg.bankroll - current_market_exposure)
    cap_sport = cfg.max_fraction_per_sport * cfg.bankroll - current_sport_exposure
    cap_total = cfg.max_total_exposure * cfg.bankroll - current_total_exposure

    for cap, label in ((cap_market, "market cap"), (cap_sport, "sport cap"), (cap_total, "total exposure cap")):
        if cap <= 0:
            return StakeDecision(0.0, 0.0, 0.0, edge, [f"{label} exhausted"])
        if stake > cap:
            stake = cap
            reasons.append(f"clipped by {label}")

    if stake < cfg.min_stake:
        return StakeDecision(0.0, 0.0, 0.0, edge, [f"stake {stake:.2f} < min {cfg.min_stake}"])

    size = stake / price
    return StakeDecision(
        stake=round(stake, 2),
        size=round(size, 2),
        kelly_fraction=stake / cfg.bankroll if cfg.bankroll > 0 else 0.0,
        edge=edge,
        reasons=reasons or ["ok"],
    )
