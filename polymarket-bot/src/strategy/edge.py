"""Edge computation, filters, and fractional Kelly sizing."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from ..clients.clob import Quote
from ..models.fair_value import FairEstimate

log = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    token_id: str
    market_question: str
    outcome_name: str
    matched_game: str
    fair_prob: float
    ask: float
    edge: float
    stake_usd: float
    shares: float


def kelly_stake(fair: float, price: float, bankroll: float, fraction: float) -> float:
    """Kelly for a binary contract bought at `price` paying 1 if it hits.

    b = (1 - price) / price;  f* = (b*p - q) / b  →  simplifies to (p - price) / (1 - price)
    """
    if price <= 0 or price >= 1:
        return 0.0
    f_star = (fair - price) / (1 - price)
    return max(0.0, f_star * fraction * bankroll)


def build_signals(
    estimates: list[FairEstimate],
    quotes: dict[str, Quote],
    strategy_cfg: dict,
) -> list[TradeSignal]:
    min_edge = float(strategy_cfg.get("min_edge", 0.04))
    kelly_fraction = float(strategy_cfg.get("kelly_fraction", 0.25))
    bankroll = float(strategy_cfg.get("bankroll_usd", 500))
    max_stake = float(strategy_cfg.get("max_stake_per_market", 50))
    max_total = float(strategy_cfg.get("max_total_exposure", 250))
    min_liquidity = float(strategy_cfg.get("min_liquidity", 500))
    min_hours = float(strategy_cfg.get("min_hours_to_event", 1))
    max_hours = float(strategy_cfg.get("max_hours_to_event", 96))
    min_price = float(strategy_cfg.get("min_price", 0.05))
    max_price = float(strategy_cfg.get("max_price", 0.95))

    now = datetime.now(timezone.utc)
    signals: list[TradeSignal] = []

    for est in estimates:
        mkt = est.market
        if mkt.liquidity < min_liquidity:
            continue
        if mkt.game_start:
            hrs = (mkt.game_start - now).total_seconds() / 3600
            if hrs < min_hours or hrs > max_hours:
                continue

        token_id = mkt.clob_token_ids[est.outcome_index]
        q = quotes.get(token_id)
        if not q or q.ask is None:
            continue
        if not (min_price <= q.ask <= max_price):
            continue

        edge = est.fair_prob - q.ask
        if edge < min_edge:
            continue

        stake = min(kelly_stake(est.fair_prob, q.ask, bankroll, kelly_fraction), max_stake)
        if stake < 1.0:
            continue

        signals.append(
            TradeSignal(
                token_id=token_id,
                market_question=mkt.question,
                outcome_name=est.outcome_name,
                matched_game=est.matched_game,
                fair_prob=round(est.fair_prob, 4),
                ask=q.ask,
                edge=round(edge, 4),
                stake_usd=round(stake, 2),
                shares=round(stake / q.ask, 2),
            )
        )

    # best edges first, then enforce total exposure cap
    signals.sort(key=lambda s: s.edge, reverse=True)
    capped, total = [], 0.0
    for s in signals:
        if total + s.stake_usd > max_total:
            continue
        capped.append(s)
        total += s.stake_usd

    log.info("signals: %d candidates, %d after exposure cap ($%.2f)", len(signals), len(capped), total)
    return capped
