"""Prediction-engine contract. A SportModel turns an upcoming event into a
probability; the bot layer never sees sport-specific details.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sportsbot.core.types import Prediction, Sport


@dataclass
class EventInput:
    """A normalized upcoming (or historical) event to predict.

    `home`/`away` are canonical participant names; for tennis/table tennis
    "home" is just side A. `context` carries sport-specific extras
    (surface, best_of, starting pitchers, league, ...).
    """

    sport: Sport
    home: str
    away: str
    start_time: Optional[datetime] = None
    best_of: int = 3
    context: dict[str, Any] = field(default_factory=dict)


class SportModel(abc.ABC):
    """Base class for all sport prediction models."""

    name: str = "base"
    sport: Sport

    @abc.abstractmethod
    def predict(self, event: EventInput) -> Prediction:
        """Return P(home/side-A wins) as Prediction.prob_yes.

        Must always return a probability; when data is thin, widen
        `uncertainty` rather than failing (the strategy layer skips
        high-uncertainty predictions).
        """

    @abc.abstractmethod
    def fit(self, history: Any) -> None:
        """Train/refresh ratings from historical results."""

    def save(self, path: str) -> None:  # pragma: no cover - overridden where used
        raise NotImplementedError

    def load(self, path: str) -> None:  # pragma: no cover
        raise NotImplementedError
