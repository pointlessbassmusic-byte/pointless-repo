"""Touch-maker arm (rank 2 of the research report; measured as Result 15).

Rest at the venue's best bid or ask on a two-sided pre-game book and let
the half-spread, net of adverse selection, be the edge. There is no model
and no fair-value claim: the intent's `prob` is the market mid and its
`edge` is the distance from the mid to the resting price less the maker
fee -- the most a fill can earn if the mid does not move against it.

Why it exists. Result 15 (Polymarket MLB, 635 games): a bid one tick
under the taker's price earned +0.6 pt CLV per fill (+1.1 to +1.8 pt
behind informed flow) with intervals above zero, but every fill was a
queue-priority fill at the existing bid, and one tick deeper the edge
vanished. The one quantity the tape cannot give is the fill share of a
NEW order at the touch. This arm measures it: minimum size, both sides,
the bot's own TTL, every fill graded by the same CLV harness as the
model arms, under the same risk layer and allocation gate. Paper by
default; the paper venue's tape-verified fills carry the same
front-of-queue caveat, so only real orders settle the question.

What it never does: cross the spread, add to a losing side, size by any
Kelly fraction, or quote inside a one-tick book when asked to improve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

from sportsbot.core.types import BetIntent, MarketInfo, MarketQuote, Side

TOUCH_PREFIX = "touch"


@dataclass
class TouchConfig:
    enabled: bool = False
    sports: tuple[str, ...] = ("baseball",)
    improve: bool = False          # False: join the touch (what Result 15 measured)
    min_spread_ticks: int = 1      # book must be at least this wide (join); improving needs room
    max_spread: float = 0.10       # wider than this the touch is not a price
    sides: str = "both"            # both | yes | no
    stake_usd: float = 1.0         # dollars per resting order: minimum size by design
    max_stake_per_market: float = 4.0   # cost basis cap per market, both sides together
    min_minutes_to_start: float = 10.0
    max_hours_to_start: float = 8.0
    min_entry_price: float = 0.15
    max_entry_price: float = 0.85
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_cfg(cls, cfg: dict) -> "TouchConfig":
        t = dict(cfg.get("touch", {}) or {})
        ex = cfg.get("execution", {}) or {}
        sports = t.get("sports", ["baseball"])
        if isinstance(sports, str):
            sports = [sports]
        sides = str(t.get("sides", "both")).lower()
        if sides not in ("both", "yes", "no"):
            raise ValueError("touch.sides must be both | yes | no")
        return cls(
            enabled=bool(t.get("enabled", False)),
            sports=tuple(str(s) for s in sports),
            improve=bool(t.get("improve", False)),
            min_spread_ticks=max(1, int(t.get("min_spread_ticks", 1))),
            max_spread=float(t.get("max_spread", 0.10)),
            sides=sides,
            stake_usd=float(t.get("stake_usd", 1.0)),
            max_stake_per_market=float(t.get("max_stake_per_market", 4.0)),
            min_minutes_to_start=float(t.get("min_minutes_to_start", 10.0)),
            max_hours_to_start=float(t.get("max_hours_to_start", 8.0)),
            min_entry_price=float(ex.get("min_entry_price", 0.15)),
            max_entry_price=float(ex.get("max_entry_price", 0.85)),
        )

    def runs(self, sport_key: str) -> bool:
        return self.enabled and sport_key in self.sports


def evaluate_touch(
    market: MarketInfo,
    quote: MarketQuote,
    cfg: TouchConfig,
    exposure: dict,
    resting_sides: Iterable[Side],
    maker_fee_fn: Callable[[float, str], float],
    now: Optional[datetime] = None,
    sport_budget: Optional[float] = None,
    style: str = "both",
) -> tuple[Optional[BetIntent], str]:
    """One resting order at the touch for `market`, or the reason not to.

    One side per call: the other side is posted on the next cycle once
    this one rests (`resting_sides` carries what is already on the book),
    so a two-sided quote builds over two cycles and never self-crosses.
    `style` is the portfolio's answer for this sport ("none" means no
    funded arm). Every check fails closed.
    """
    sport_key = market.sport.value if market.sport else "unknown"
    if not cfg.runs(sport_key):
        return None, "touch arm not enabled for this sport"
    if style not in ("both", "maker"):
        return None, "no funded maker arm for this sport (portfolio allocation)"
    if quote.bid is None or quote.ask is None:
        return None, "no two-sided book"
    tick = market.tick_size or 0.01
    spread = quote.ask - quote.bid
    if spread <= 0:
        return None, "crossed or empty book"
    ticks = int(round(spread / tick))
    if ticks < cfg.min_spread_ticks:
        return None, f"spread {spread:.3f} is under {cfg.min_spread_ticks} tick(s)"
    if spread > cfg.max_spread + 1e-9:
        return None, f"spread {spread:.3f} wider than the {cfg.max_spread:.3f} touch limit"
    mid = (quote.bid + quote.ask) / 2.0
    if not (cfg.min_entry_price <= mid <= cfg.max_entry_price):
        return None, (f"mid {mid:.3f} outside the tradable band "
                      f"[{cfg.min_entry_price:.2f}, {cfg.max_entry_price:.2f}]")
    now = now or datetime.now(timezone.utc)
    if market.start_time is None:
        return None, "start time unknown — a touch order cannot rest into the game"
    start = market.start_time
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    mins = (start - now).total_seconds() / 60.0
    if mins < cfg.min_minutes_to_start:
        return None, f"too close to start ({mins:.0f}m)"
    if mins > cfg.max_hours_to_start * 60.0:
        return None, f"start {mins / 60.0:.1f}h away — outside the quoting window"
    held = float((exposure.get("by_market") or {}).get(market.market_id, 0.0))
    if held + cfg.stake_usd > cfg.max_stake_per_market + 1e-9:
        return None, (f"market cost basis {held:.2f} + {cfg.stake_usd:.2f} would pass "
                      f"the {cfg.max_stake_per_market:.2f} touch cap")
    if sport_budget is not None:
        in_sport = float((exposure.get("by_sport") or {}).get(sport_key, 0.0))
        if in_sport + cfg.stake_usd > sport_budget + 1e-9:
            return None, f"sport budget {sport_budget:.2f} would be exceeded"

    # Prices in each side's own frame. Joining rests AT the touch; improving
    # rests one tick inside and needs the two improved levels to stay apart
    # (a YES bid at p and a NO bid at 1-p are the same level and would match
    # each other), so on a two-tick book only one side can improve.
    if cfg.improve:
        yes_px = round(quote.bid + tick, 4)
        no_px = round((1.0 - quote.ask) + tick, 4)
        if ticks < 2:
            return None, "one-tick book: nowhere to improve without crossing"
        both_fit = ticks >= 3
    else:
        yes_px = round(quote.bid, 4)
        no_px = round(1.0 - quote.ask, 4)
        both_fit = True

    resting = set(resting_sides)
    wanted = [Side.YES, Side.NO] if cfg.sides == "both" else (
        [Side.YES] if cfg.sides == "yes" else [Side.NO])
    for side in wanted:
        if side in resting:
            continue
        if not both_fit and side == Side.NO and Side.YES in resting:
            return None, "two-tick book: the improved YES side already rests"
        price = yes_px if side == Side.YES else no_px
        if not (0.0 < price < 1.0):
            continue
        prob = mid if side == Side.YES else 1.0 - mid
        fee = float(maker_fee_fn(price, market.market_id))
        edge = prob - price - fee
        if edge <= 0:
            return None, f"maker fee {fee:.4f} eats the half-spread {prob - price:.4f}"
        size = round(cfg.stake_usd / price, 2)
        if size <= 0:
            continue
        mode = "improve" if cfg.improve else "join"
        reason = (f"{TOUCH_PREFIX} {mode} bid={quote.bid:.3f} ask={quote.ask:.3f} "
                  f"spread={spread:.3f} half={prob - price:.4f} fee={fee:.4f} maker")
        return BetIntent(market=market, side=side, prob=prob, price=price, size=size,
                         edge=edge, kelly_fraction=0.0, reason=reason), reason
    if wanted and all(s in resting for s in wanted):
        return None, "both sides already resting"
    return None, "nothing to post"
