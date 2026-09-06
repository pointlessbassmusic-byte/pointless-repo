"""Shared domain types. Every layer speaks these; exchange clients translate
venue-specific payloads into them at the boundary.

Price convention: binary-market prices are probabilities in [0, 1]
(Polymarket native; Kalshi cents are divided by 100 at the client boundary).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


class Sport(str, enum.Enum):
    TENNIS = "tennis"
    BASEBALL = "baseball"
    TABLE_TENNIS = "table_tennis"


class Exchange(str, enum.Enum):
    POLYMARKET = "polymarket"
    KALSHI = "kalshi"
    PAPER = "paper"


class Side(str, enum.Enum):
    """Side of a binary market. YES = buy the outcome token / yes contract."""

    YES = "yes"
    NO = "no"


class OrderType(str, enum.Enum):
    LIMIT = "limit"        # rest at price (GTC)
    LIMIT_GTD = "limit_gtd"  # rest until expiration
    FOK = "fok"            # fill-or-kill (taker)
    IOC = "ioc"            # immediate-or-cancel


class OrderStatus(str, enum.Enum):
    PENDING = "pending"        # created locally, not yet acknowledged
    OPEN = "open"              # resting on the book
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BookLevel(BaseModel):
    price: float  # probability units [0,1]
    size: float   # shares / contracts


class MarketQuote(BaseModel):
    """Top-of-book snapshot for the YES side of a binary market."""

    market_id: str
    bid: Optional[float] = None   # best YES bid
    ask: Optional[float] = None   # best YES ask
    bids: list[BookLevel] = Field(default_factory=list)  # descending price
    asks: list[BookLevel] = Field(default_factory=list)  # ascending price
    ts: datetime = Field(default_factory=utcnow)

    @property
    def mid(self) -> Optional[float]:
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2.0
        return self.bid if self.bid is not None else self.ask

    @property
    def spread(self) -> Optional[float]:
        if self.bid is not None and self.ask is not None:
            return self.ask - self.bid
        return None

    def depth_at(self, side: Side, max_levels: int = 5) -> float:
        levels = self.asks if side == Side.YES else self.bids
        return sum(lvl.size for lvl in levels[:max_levels])


class MarketInfo(BaseModel):
    """A single binary market (one outcome pair) on a venue.

    For a Polymarket sports event (e.g. "Sinner vs. Alcaraz") each market is
    a moneyline binary; `home`/`away` carry the canonical participant names
    used for matching against model entities.
    """

    exchange: Exchange
    market_id: str                  # Polymarket: condition_id; Kalshi: ticker
    yes_token_id: Optional[str] = None   # Polymarket CLOB token for YES/outcome-A
    no_token_id: Optional[str] = None
    question: str = ""
    slug: str = ""
    sport: Optional[Sport] = None
    home: Optional[str] = None      # participant the YES side refers to
    away: Optional[str] = None
    start_time: Optional[datetime] = None
    close_time: Optional[datetime] = None
    active: bool = True
    tick_size: float = 0.01
    min_order_size: float = 5.0     # venue minimum, in shares/contracts
    neg_risk: bool = False          # Polymarket negative-risk market flag
    meta: dict[str, Any] = Field(default_factory=dict)


class Prediction(BaseModel):
    """Model output for one market: probability that YES resolves true."""

    market_id: str
    sport: Sport
    model: str                     # e.g. "tennis_elo_markov_v1"
    prob_yes: float                # calibrated model probability
    prob_raw: Optional[float] = None   # pre-blend model probability
    uncertainty: float = 0.0       # stdev-like estimate of prob error
    features: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=utcnow)


class BetIntent(BaseModel):
    """A sized, priced desire to trade — produced by strategy, consumed by executor."""

    intent_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    market: MarketInfo
    side: Side
    prob: float                    # our estimated prob for the side we buy
    price: float                   # limit price we are willing to pay (prob units)
    size: float                    # shares/contracts to buy
    edge: float                    # prob - price, after adjustments
    kelly_fraction: float          # fraction of bankroll this represents
    reason: str = ""
    ts: datetime = Field(default_factory=utcnow)


class Order(BaseModel):
    order_id: str = ""             # venue id once acknowledged
    client_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    intent_id: Optional[str] = None
    exchange: Exchange = Exchange.PAPER
    market_id: str = ""
    token_id: Optional[str] = None
    side: Side = Side.YES
    order_type: OrderType = OrderType.LIMIT
    price: float = 0.0
    size: float = 0.0
    filled: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    raw: dict[str, Any] = Field(default_factory=dict)


class Fill(BaseModel):
    order_id: str
    market_id: str
    side: Side
    price: float
    size: float
    fee: float = 0.0
    ts: datetime = Field(default_factory=utcnow)


class Position(BaseModel):
    market_id: str
    side: Side
    size: float = 0.0              # shares held
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    @property
    def cost(self) -> float:
        return self.size * self.avg_price
