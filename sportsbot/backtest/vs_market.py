"""Does the model beat the venue's own price?

This is the only question that decides whether a strategy can make money,
and until this module existed nothing in the repo asked it. `backtest/
engine.py` scores a model against coin/always-home baselines; a model can
beat those comfortably and still lose to the line on every single market.
Measured 2026-09-23, that is exactly what both sports models do.

Method: for each settled market, take the venue price at several LEAD
TIMES before the event, score market and model against the realised
outcome, and settle a flat stake at the model's claimed edge.

Two traps this is built to avoid:

* **In-play contamination.** Kalshi's `occurrence_datetime` is not
  reliably first pitch/serve: prices sampled at lead 0 scored Brier 0.063
  with 91% accuracy, which is a market that has already seen the result.
  Scoring a single lead can therefore read as either an unbeatable market
  or a broken model. Sweeping the lead makes the contamination visible --
  a Brier that collapses as the lead shrinks is the tell -- so callers
  compare at a lead they can defend.
* **Fitting on the test set.** Ratings bootstrapped from settled markets
  are fitted on the very markets being scored. `cutoff` exists so the
  caller states the fit horizon explicitly; events at or before it are
  dropped rather than quietly inflating the result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

# Leads in hours. 0 is deliberately included: it is the contamination
# canary, not a tradable sample.
DEFAULT_LEADS = (24, 12, 6, 3, 1, 0)


@dataclass
class MarketSample:
    """One settled market with its pre-event price path."""

    market_id: str
    won: bool                       # did the YES side win
    prices: dict[int, float]        # lead hours -> YES price
    model_prob: Optional[float] = None
    meta: dict = field(default_factory=dict)


@dataclass
class LeadScore:
    lead_hours: int
    n: int
    market_brier: float
    model_brier: float
    coin_brier: float
    market_logloss: float
    model_logloss: float
    market_acc: float
    model_acc: float

    @property
    def model_beats_market(self) -> bool:
        return self.model_brier < self.market_brier

    @property
    def edge_brier(self) -> float:
        """Positive means the model is better. Negative means no strategy."""
        return self.market_brier - self.model_brier


@dataclass
class StakeResult:
    min_edge: float
    bets: int
    wins: int
    pnl: float

    @property
    def win_rate(self) -> float:
        return self.wins / self.bets if self.bets else 0.0

    @property
    def roi(self) -> float:
        """PnL per contract staked. The number that decides go/no-go."""
        return self.pnl / self.bets if self.bets else 0.0


def _brier(p: float, y: float) -> float:
    return (p - y) ** 2


def _logloss(p: float, y: float) -> float:
    p = min(max(p, 1e-6), 1.0 - 1e-6)
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))


def score_leads(samples: Iterable[MarketSample],
                leads: Iterable[int] = DEFAULT_LEADS) -> list[LeadScore]:
    """Market vs model at each lead, longest lead first."""
    acc: dict[int, dict] = {}
    for s in samples:
        if s.model_prob is None:
            continue
        y = 1.0 if s.won else 0.0
        for lead in leads:
            px = s.prices.get(lead)
            if px is None:
                continue
            a = acc.setdefault(lead, {"n": 0, "mb": 0.0, "db": 0.0, "cb": 0.0,
                                      "ml": 0.0, "dl": 0.0, "ma": 0, "da": 0})
            a["n"] += 1
            a["mb"] += _brier(px, y)
            a["db"] += _brier(s.model_prob, y)
            a["cb"] += _brier(0.5, y)
            a["ml"] += _logloss(px, y)
            a["dl"] += _logloss(s.model_prob, y)
            a["ma"] += int((px > 0.5) == (y > 0.5))
            a["da"] += int((s.model_prob > 0.5) == (y > 0.5))
    out = []
    for lead in sorted(acc, reverse=True):
        a = acc[lead]
        n = a["n"]
        out.append(LeadScore(
            lead_hours=lead, n=n,
            market_brier=a["mb"] / n, model_brier=a["db"] / n,
            coin_brier=a["cb"] / n,
            market_logloss=a["ml"] / n, model_logloss=a["dl"] / n,
            market_acc=a["ma"] / n, model_acc=a["da"] / n,
        ))
    return out


def suspect_in_play(scores: list[LeadScore], drop: float = 0.05) -> set[int]:
    """Leads whose market Brier collapses vs the longest clean lead.

    A price that already knows the result scores far better than any
    pre-event line can. Flagging is by comparison with the longest lead
    rather than an absolute threshold, because a fair Brier depends on how
    lopsided the slate is.
    """
    if not scores:
        return set()
    baseline = scores[0].market_brier
    return {s.lead_hours for s in scores
            if baseline - s.market_brier > drop}


def clv_correlation(samples: Iterable[MarketSample],
                    early: int = 24, late: int = 3) -> Optional[dict]:
    """Does model disagreement at `early` predict the line move to `late`?

    This is the softer test a model can pass while still losing to the
    close: a model with no edge against the closing line may still be
    early to where the line ends up. Correlation near zero means it is
    not, and there is nothing to trade.
    """
    xs, ys = [], []
    for s in samples:
        if s.model_prob is None:
            continue
        pe, pl = s.prices.get(early), s.prices.get(late)
        if pe is None or pl is None:
            continue
        xs.append(s.model_prob - pe)
        ys.append(pl - pe)
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    vx = sum((a - mx) ** 2 for a in xs)
    vy = sum((b - my) ** 2 for b in ys)
    corr = cov / math.sqrt(vx * vy) if vx > 0 and vy > 0 else 0.0
    agree = [(a, b) for a, b in zip(xs, ys) if abs(a) >= 0.05 and abs(b) > 1e-9]
    toward = sum(1 for a, b in agree if (a > 0) == (b > 0))
    return {
        "n": n, "corr": corr,
        "beta": cov / vx if vx > 0 else 0.0,
        "mean_abs_disagreement": sum(abs(a) for a in xs) / n,
        "mean_abs_line_move": sum(abs(b) for b in ys) / n,
        "n_material": len(agree),
        "moved_toward_model": toward / len(agree) if agree else None,
    }


def flat_stake(samples: Iterable[MarketSample], lead: int,
               fee_fn: Callable[[float], float],
               min_edges: Iterable[float] = (0.03, 0.05, 0.08, 0.10),
               ) -> list[StakeResult]:
    """Settle one contract per qualifying side at the `lead` price.

    Both sides are considered: an edge on NO is as tradable as one on YES,
    and testing only YES would hide half the strategy. Fees are charged on
    every bet -- a backtest that skips them is the failure this repo's
    archive already documents.
    """
    out = []
    samples = list(samples)
    for thr in min_edges:
        bets = wins = 0
        pnl = 0.0
        for s in samples:
            if s.model_prob is None:
                continue
            px = s.prices.get(lead)
            if px is None:
                continue
            y = 1.0 if s.won else 0.0
            for prob, price, won in ((s.model_prob, px, y == 1.0),
                                     (1.0 - s.model_prob, 1.0 - px, y == 0.0)):
                if not (0.0 < price < 1.0) or prob - price < thr:
                    continue
                fee = fee_fn(price)
                bets += 1
                wins += int(won)
                pnl += (1.0 - price - fee) if won else (-price - fee)
        out.append(StakeResult(min_edge=thr, bets=bets, wins=wins, pnl=pnl))
    return out


def price_at_lead(candles: Iterable[dict], event_start: datetime,
                  leads: Iterable[int] = DEFAULT_LEADS) -> dict[int, float]:
    """Last close at or before each lead, from Kalshi 60-minute candles.

    Takes the last candle NOT after the target so the price is one the
    market actually showed by then; a candle straddling the target would
    borrow information from after it.
    """
    if event_start.tzinfo is None:
        event_start = event_start.replace(tzinfo=timezone.utc)
    rows = []
    for k in candles:
        ts = k.get("end_period_ts")
        px = (k.get("price") or {}).get("close_dollars")
        if ts is None or px in (None, ""):
            continue
        rows.append((float(ts), float(px)))
    rows.sort()
    out: dict[int, float] = {}
    for lead in leads:
        target = (event_start - timedelta(hours=lead)).timestamp()
        best = None
        for ts, px in rows:
            if ts > target:
                break
            best = px
        if best is not None:
            out[lead] = best
    return out


def format_report(scores: list[LeadScore], stakes: list[StakeResult],
                  clv: Optional[dict], suspect: set[int]) -> str:
    lines = [
        f"{'lead':>5} {'n':>5} {'mkt Brier':>10} {'mdl Brier':>10} "
        f"{'coin':>7} {'mkt LL':>8} {'mdl LL':>8} {'mkt acc':>8} {'mdl acc':>8}"
    ]
    for s in scores:
        flag = "  <- in-play?" if s.lead_hours in suspect else ""
        lines.append(
            f"{s.lead_hours:>4}h {s.n:>5} {s.market_brier:>10.4f} "
            f"{s.model_brier:>10.4f} {s.coin_brier:>7.4f} "
            f"{s.market_logloss:>8.4f} {s.model_logloss:>8.4f} "
            f"{s.market_acc:>8.3f} {s.model_acc:>8.3f}{flag}")
    if clv:
        lines.append("")
        lines.append(f"CLV: corr={clv['corr']:+.4f} beta={clv['beta']:+.4f} "
                     f"mean|disagree|={clv['mean_abs_disagreement']:.4f} "
                     f"mean|move|={clv['mean_abs_line_move']:.4f}")
        if clv["moved_toward_model"] is not None:
            lines.append(f"     line moved toward model in "
                         f"{clv['moved_toward_model']:.3f} of {clv['n_material']} "
                         f"material disagreements (0.5 = no information)")
    if stakes:
        lines.append("")
        lines.append("flat stake, 1 contract per qualifying side, fees charged:")
        for r in stakes:
            if r.bets:
                lines.append(f"  edge>={r.min_edge:.2f}: bets={r.bets:>4} "
                             f"win%={r.win_rate:.3f} PnL=${r.pnl:+.2f} "
                             f"ROI={r.roi * 100:+.2f}%/contract")
            else:
                lines.append(f"  edge>={r.min_edge:.2f}: no qualifying bets")
    return "\n".join(lines)


def verdict(scores: list[LeadScore], stakes: list[StakeResult],
            suspect: set[int]) -> str:
    """One line an operator can act on. Says NO when the data says no."""
    clean = [s for s in scores if s.lead_hours not in suspect and s.lead_hours > 0]
    if not clean:
        return "NO CLEAN LEAD — every sampled price looks in-play contaminated."
    best = max(clean, key=lambda s: s.edge_brier)
    profitable = [r for r in stakes if r.bets and r.roi > 0]
    if best.edge_brier <= 0:
        return (f"NO EDGE — the model loses to the line at every clean lead "
                f"(best {best.lead_hours}h: {best.edge_brier:+.4f} Brier). "
                f"Do not trade this model.")
    if not profitable:
        return (f"NO EDGE — the model beats the line on Brier at {best.lead_hours}h "
                f"({best.edge_brier:+.4f}) but no stake threshold is profitable "
                f"after fees.")
    top = max(profitable, key=lambda r: r.roi)
    return (f"POSSIBLE EDGE — {best.lead_hours}h Brier {best.edge_brier:+.4f}; "
            f"best stake edge>={top.min_edge:.2f} ROI {top.roi * 100:+.2f}% on "
            f"{top.bets} bets. Needs a second out-of-sample window before any "
            f"live decision.")
