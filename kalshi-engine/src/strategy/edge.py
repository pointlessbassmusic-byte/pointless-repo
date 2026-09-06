"""Edge + fractional Kelly for Kalshi binary contracts (can take YES or NO side)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..client import Market
from ..substrate.ensemble import EnsembleResult

log = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    ticker: str
    title: str
    side: str            # "yes" | "no"
    fair_prob: float     # prob of the side we're buying
    price: float         # price of the side we're buying (0-1)
    edge: float
    stake_usd: float
    count: int           # contracts
    rationale: str


def kelly_stake(fair: float, price: float, bankroll: float, fraction: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    f_star = (fair - price) / (1 - price)
    return max(0.0, f_star * fraction * bankroll)


def build_signals(
    results: list[EnsembleResult],
    strategy_cfg: dict,
    exclude_tickers: set[str] = frozenset(),
    existing_exposure: float = 0.0,
) -> list[TradeSignal]:
    """exclude_tickers: markets with a live order already placed (never re-order);
    existing_exposure: USD already committed, counted against max_total_exposure."""
    min_edge = float(strategy_cfg.get("min_edge", 0.05))
    kelly_fraction = float(strategy_cfg.get("kelly_fraction", 0.25))
    bankroll = float(strategy_cfg.get("bankroll_usd", 500))
    max_stake = float(strategy_cfg.get("max_stake_per_market", 40))
    max_total = float(strategy_cfg.get("max_total_exposure", 200))
    min_price = float(strategy_cfg.get("min_price", 0.05))
    max_price = float(strategy_cfg.get("max_price", 0.95))

    signals: list[TradeSignal] = []
    for res in results:
        m: Market = res.market
        if m.ticker in exclude_tickers:
            continue
        # YES side: pay the yes ask; NO side: pay (1 - yes_bid)
        candidates = []
        if m.yes_ask > 0:
            candidates.append(("yes", res.prob_yes, m.yes_ask))
        if m.yes_bid > 0:
            candidates.append(("no", 1 - res.prob_yes, 1 - m.yes_bid))

        for side, fair, price in candidates:
            if not (min_price <= price <= max_price):
                continue
            edge = fair - price
            if edge < min_edge:
                continue
            stake = min(kelly_stake(fair, price, bankroll, kelly_fraction), max_stake)
            count = int(stake / price)
            if count < 1:
                continue
            rationale = "; ".join(f"{f.generator}:{f.prob_yes:.2f}" for f in res.forecasts)
            signals.append(
                TradeSignal(
                    ticker=m.ticker, title=m.title, side=side,
                    fair_prob=round(fair, 4), price=round(price, 4),
                    edge=round(edge, 4), stake_usd=round(count * price, 2),
                    count=count, rationale=rationale,
                )
            )
            break  # at most one side per market

    signals.sort(key=lambda s: s.edge, reverse=True)
    capped, total = [], existing_exposure
    for s in signals:
        if total + s.stake_usd > max_total:
            continue
        capped.append(s)
        total += s.stake_usd

    log.info("signals: %d candidates, %d after exposure cap ($%.2f)", len(signals), len(capped), total)
    return capped
