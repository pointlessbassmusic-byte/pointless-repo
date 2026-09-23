"""Is passive quoting profitable? Mark-out on maker fills.

Spread capture is the one edge hypothesis that does not require
out-forecasting anyone: quote both sides, earn the spread, stay flat. This
module tests it against the venue's own trade tape, because "the spread is
2 cents" says nothing about what a resting order actually earns.

Kalshi's trade tape identifies which resting order was filled:

    taker_book_side == "ask"  taker SOLD yes  -> a resting BID filled
                                                 (the maker BOUGHT yes)
    taker_book_side == "bid"  taker BOUGHT yes -> a resting ASK filled
                                                 (the maker SOLD yes)

Mark-out is the fill marked to a later trade price. Positive means the
price moved the maker's way; negative means the maker was picked off.

Three things this is built to report honestly:

* **Fill imbalance.** A maker quoting both sides expects roughly balanced
  fills. When one side dominates, the position is directional, not flat,
  and any pooled average mixes spread capture with price drift instead of
  cancelling it. Measured on 500k pre-match tennis fills the split was
  78/22, so the pooled mean there was drift, not edge.
* **Median as well as mean.** Mark-outs are fat-tailed. A positive mean
  with a zero median is a few large winners carrying a mass of fills that
  captured nothing, which is the opposite of a market maker's profile and
  is not a business.
* **The fee.** Kalshi's sports series charge makers, so gross has to clear
  the maker fee before a fill pays at all.

Marking to the next TRADE price is deliberately conservative: it credits
the maker only at a price someone actually dealt at, rather than at a mid
the maker may not be able to exit against.
"""

from __future__ import annotations

import statistics
from bisect import bisect_right
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

# Which resting side a taker's book side implies was filled.
MAKER_BOUGHT = "ask"
MAKER_SOLD = "bid"


@dataclass
class Fill:
    ts: float
    price: float                # YES price the trade printed at
    taker_book_side: str
    is_block: bool = False

    @property
    def maker_bought(self) -> Optional[bool]:
        if self.taker_book_side == MAKER_BOUGHT:
            return True
        if self.taker_book_side == MAKER_SOLD:
            return False
        return None


@dataclass
class MarkoutStats:
    horizon_s: int
    n: int
    mean: float
    median: float
    maker_fee: float
    n_buy: int
    n_sell: int
    frac_positive: float
    frac_zero: float

    @property
    def net_mean(self) -> float:
        return self.mean - self.maker_fee

    @property
    def net_median(self) -> float:
        return self.median - self.maker_fee

    @property
    def buy_share(self) -> float:
        tot = self.n_buy + self.n_sell
        return self.n_buy / tot if tot else 0.0

    @property
    def balanced(self) -> bool:
        """Roughly two-sided flow. When false, the mean mixes in drift."""
        return 0.45 <= self.buy_share <= 0.55

    @property
    def pays(self) -> bool:
        """The typical fill clears the fee. Median, not mean, on purpose."""
        return self.net_median > 0


def effective_spread(fills: Sequence[Fill], cap: float = 0.25) -> Optional[float]:
    """Median |price change| when the taker flips side — the spread a maker
    could capture per round trip with no adverse selection. Half of it is
    the most one passive leg can earn."""
    diffs = []
    for i in range(1, len(fills)):
        if fills[i].taker_book_side != fills[i - 1].taker_book_side:
            d = abs(fills[i].price - fills[i - 1].price)
            if 0.0 < d <= cap:
                diffs.append(d)
    return statistics.median(diffs) if diffs else None


def markout(fills: Sequence[Fill], horizon_s: int,
            maker_fee: float) -> Optional[MarkoutStats]:
    """Mark every maker fill to the last trade within `horizon_s`."""
    usable = [f for f in fills if not f.is_block and f.maker_bought is not None]
    if len(usable) < 2:
        return None
    ts = [f.ts for f in usable]
    px = [f.price for f in usable]
    vals: list[float] = []
    n_buy = n_sell = 0
    for i, f in enumerate(usable):
        bought = f.maker_bought
        if bought:
            n_buy += 1
        else:
            n_sell += 1
        j = bisect_right(ts, ts[i] + horizon_s) - 1
        if j <= i:
            continue
        vals.append((px[j] - f.price) if bought else (f.price - px[j]))
    if not vals:
        return None
    return MarkoutStats(
        horizon_s=horizon_s, n=len(vals),
        mean=statistics.fmean(vals), median=statistics.median(vals),
        maker_fee=maker_fee, n_buy=n_buy, n_sell=n_sell,
        frac_positive=sum(1 for v in vals if v > 0) / len(vals),
        frac_zero=sum(1 for v in vals if v == 0) / len(vals),
    )


def verdict(stats: Iterable[MarkoutStats], half_spread: Optional[float]) -> str:
    """One line. Says no when passive quoting does not pay."""
    stats = [s for s in stats if s]
    if not stats:
        return "NO DATA — not enough maker fills to judge."
    short = min(stats, key=lambda s: s.horizon_s)
    lines = []
    if not short.balanced:
        lines.append(
            f"FLOW IS ONE-SIDED ({short.buy_share:.0%} buys): quoting both "
            f"sides accumulates a directional position rather than staying "
            f"flat, and pooled means mix in drift.")
    if half_spread is not None and short.median <= 0:
        lines.append(
            f"ADVERSE SELECTION TAKES THE SPREAD — theoretical capture "
            f"{half_spread:+.5f}/contract, realised median {short.median:+.5f}.")
    if not any(s.pays for s in stats):
        lines.append(
            f"NO EDGE — the median fill never clears the maker fee "
            f"({short.maker_fee:.4f}); net median "
            f"{short.net_median:+.5f}/contract. Do not quote this passively.")
    else:
        best = max((s for s in stats if s.pays), key=lambda s: s.net_median)
        lines.append(
            f"POSSIBLE EDGE at {best.horizon_s}s: net median "
            f"{best.net_median:+.5f}/contract on {best.n} fills. Needs a "
            f"second window and a fill-probability model before any live "
            f"decision.")
    return " ".join(lines)
