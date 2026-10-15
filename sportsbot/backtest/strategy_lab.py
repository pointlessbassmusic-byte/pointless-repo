"""Candidate strategies, each as a backtestable function over real venue data.

Every strategy here was proposed either from the published playbook for
prediction and sports markets or from patterns in this repo's own samples,
and every one is measured the same way: one entry per event (thousands of
trades in one market share a single outcome, so per-trade statistics would
be fake), taker costs charged on every leg, settled against the venue's own
resolution. None is wired into trading. The results are recorded in
`docs/STRATEGY_LAB_2026-09-29.md`; re-run them as data accumulates.

The one idea worth stating up front, because it decides most of these:

**The opposite of a losing trade is not a winning trade.** A trade and its
inverse split the market's move between them, but both pay the round-trip
cost — spread plus fee — so their returns sum to minus that cost, not to
zero (`inversion_gap`). Inverting a loser only pays when it lost by MORE
than the cost of trading, i.e. when its signal was genuinely anti-predictive
and not merely uninformative. Measured here, that was true in one sample and
gone in the next.
"""

from __future__ import annotations

import math
import statistics
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

FeeFn = Callable[[float], float]


def kalshi_taker(mult: float = 1.0) -> FeeFn:
    """Marginal Kalshi taker fee per contract at price p."""
    return lambda p: 0.07 * mult * p * (1.0 - p)


@dataclass
class Outcome:
    pnls: list[float]

    @property
    def n(self) -> int:
        return len(self.pnls)

    @property
    def mean(self) -> float:
        return statistics.fmean(self.pnls) if self.pnls else 0.0

    @property
    def t(self) -> float:
        if self.n < 2:
            return 0.0
        sd = statistics.stdev(self.pnls)
        return self.mean / (sd / math.sqrt(self.n)) if sd > 0 else 0.0

    @property
    def losses(self) -> int:
        return sum(1 for p in self.pnls if p < 0)


def settle_taker(price_side: float, won: bool, fee: FeeFn,
                 slip: float = 0.005) -> float:
    """Buy one contract of a side at `price_side` (+slip) and hold to settlement."""
    c = min(0.995, price_side + slip)
    return ((1.0 - c) if won else -c) - fee(c)


# ---------------------------------------------------------------------------
# 1. Follow vs fade a model — and why both can lose
# ---------------------------------------------------------------------------
def follow_and_fade(games: Iterable[tuple[float, float, bool]], min_edge: float,
                    fee: FeeFn, slip: float = 0.005) -> tuple[Outcome, Outcome]:
    """games: (model_prob_yes, market_price_yes, yes_won). Bets the side the
    model prefers (follow) and the other side (fade) on the SAME markets."""
    fol, fad = [], []
    for q, px, y in games:
        if not (0.03 < px < 0.97) or abs(q - px) < min_edge:
            continue
        model_yes = q > px
        fol.append(settle_taker(px if model_yes else 1 - px, y if model_yes else not y, fee, slip))
        fad.append(settle_taker(1 - px if model_yes else px, (not y) if model_yes else y, fee, slip))
    return Outcome(fol), Outcome(fad)


def inversion_gap(follow: Outcome, fade: Outcome) -> float:
    """follow + fade per contract. ≈ −(spread + fees): the toll both sides pay.
    Fade is profitable only where follow lost by more than this."""
    return follow.mean + fade.mean


# ---------------------------------------------------------------------------
# 2. Sibling overround arbitrage (one market per competitor, separate books)
# ---------------------------------------------------------------------------
def sibling_lock_cost(ask_a: float, ask_b: float, bid_a: float, bid_b: float,
                      fee: FeeFn) -> float:
    """All-in cost to lock $1 across the two sibling markets of an event:
    buy both YES (exactly one pays) or buy both NO (exactly one pays).
    < 1.0 is a locked profit."""
    yes = ask_a + ask_b + fee(ask_a) + fee(ask_b)
    no_a, no_b = 1.0 - bid_a, 1.0 - bid_b
    no = no_a + no_b + fee(no_a) + fee(no_b)
    return min(yes, no)


# ---------------------------------------------------------------------------
# 3. Endgame: buy the near-certain leader in play and hold
# ---------------------------------------------------------------------------
def endgame(events: Iterable[tuple[Sequence[tuple[float, float]], float, bool]],
            threshold: float, fee: FeeFn, slip: float = 0.005) -> Outcome:
    """events: (in-play tape [(ts, yes_price)], unused_anchor, yes_won).
    First print where max(p, 1-p) >= threshold: buy the leader, hold."""
    out = []
    for tape, _, y in events:
        for _, p in tape:
            if p >= threshold:
                out.append(settle_taker(p, y, fee, slip))
                break
            if p <= 1.0 - threshold:
                out.append(settle_taker(1.0 - p, not y, fee, slip))
                break
    return Outcome(out)


# ---------------------------------------------------------------------------
# 4. In-play overreaction: fade (or follow) a sharp move, exit after H
# ---------------------------------------------------------------------------
def overreaction(tapes: Iterable[Sequence[tuple[float, float]]], jump: float,
                 lookback_s: float, hold_s: float, fee: FeeFn,
                 half_spread: float = 0.005) -> tuple[Outcome, Outcome]:
    """First move >= jump within lookback_s per tape; returns (fade, momentum),
    each a round trip exited after hold_s with spread and fee on both legs."""
    fade, mom = [], []
    for tape in tapes:
        ts = [t for t, _ in tape]
        px = [p for _, p in tape]
        for i in range(len(tape)):
            move = px[i] - px[bisect_left(ts, ts[i] - lookback_s)]
            if abs(move) < jump or not (0.10 < px[i] < 0.90):
                continue
            j = bisect_right(ts, ts[i] + hold_s) - 1
            if j <= i:
                break
            e, x = px[i], px[j]
            cost = 2 * half_spread + fee(e) + fee(x)
            long_yes = (x - e) - cost
            long_no = (e - x) - cost
            fade.append(long_no if move > 0 else long_yes)
            mom.append(long_yes if move > 0 else long_no)
            break
    return Outcome(fade), Outcome(mom)


# ---------------------------------------------------------------------------
# 5. Cross-venue: trade venue A toward venue B's price
# ---------------------------------------------------------------------------
def toward_other_venue(pairs: Iterable[tuple[float, float, bool]], min_gap: float,
                       fee: FeeFn, slip: float = 0.005) -> Outcome:
    """pairs: (price_here, price_other, yes_won), same team, same moment.
    When the other venue rates YES higher by >= min_gap, buy YES here; lower,
    buy NO here. The other venue is information only — nothing is placed there."""
    out = []
    for here, other, y in pairs:
        g = other - here
        if abs(g) < min_gap or not (0.03 < here < 0.97):
            continue
        out.append(settle_taker(here, y, fee, slip) if g > 0
                   else settle_taker(1.0 - here, not y, fee, slip))
    return Outcome(out)


def brier(pairs: Iterable[tuple[float, bool]]) -> Optional[float]:
    v = [(p - (1.0 if y else 0.0)) ** 2 for p, y in pairs]
    return statistics.fmean(v) if v else None
