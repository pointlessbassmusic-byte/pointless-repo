"""Generic Elo rating engine used by all three sport models.

Supports:
- dynamic K-factor schedules (e.g. FiveThirtyEight tennis: K = c / (n + o)^s)
- per-key rating stores with match counts and last-played timestamps
- pluggable home advantage / contextual adjustment at prediction time
- serialization to/from dict for persistence
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional


def expected_score(rating_a: float, rating_b: float) -> float:
    """P(A beats B) under the logistic Elo curve (400-point scale)."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def prob_to_elo_diff(p: float) -> float:
    """Invert expected_score: probability -> rating difference."""
    p = max(1e-6, min(1.0 - 1e-6, p))
    return -400.0 * math.log10(1.0 / p - 1.0)


def fte_tennis_k(matches_played: int, coeff: float = 250.0, offset: float = 5.0,
                 shape: float = 0.4) -> float:
    """FiveThirtyEight-style decaying K for tennis: K = 250/(n+5)^0.4.

    New players move fast (~130 pts swing early), veterans stabilize (~K 20-25).
    """
    return coeff / (matches_played + offset) ** shape


@dataclass
class PlayerRating:
    rating: float = 1500.0
    matches: int = 0
    last_played: Optional[datetime] = None


@dataclass
class EloEngine:
    initial_rating: float = 1500.0
    k_factor: float = 32.0
    # If set, called with the *winner's/loser's* match count to get their K.
    k_schedule: Optional[Callable[[int], float]] = None
    ratings: dict[str, PlayerRating] = field(default_factory=dict)

    def get(self, key: str) -> PlayerRating:
        if key not in self.ratings:
            self.ratings[key] = PlayerRating(rating=self.initial_rating)
        return self.ratings[key]

    def rating(self, key: str) -> float:
        return self.get(key).rating

    def predict(self, key_a: str, key_b: str, advantage_a: float = 0.0) -> float:
        """P(A wins). `advantage_a` is added to A's rating (home court, SP edge…)."""
        return expected_score(self.rating(key_a) + advantage_a, self.rating(key_b))

    def _k_for(self, key: str) -> float:
        if self.k_schedule is not None:
            return self.k_schedule(self.get(key).matches)
        return self.k_factor

    def update(
        self,
        winner: str,
        loser: str,
        when: Optional[datetime] = None,
        winner_advantage: float = 0.0,
        mov_multiplier: float = 1.0,
    ) -> tuple[float, float]:
        """Record a result; returns (new_winner_rating, new_loser_rating).

        `winner_advantage` is the contextual advantage the winner had (e.g.
        +home_adv if the winner was at home, -home_adv if away) so the update
        doesn't double-count context into skill.
        `mov_multiplier` scales the update for margin of victory.
        """
        w, lo = self.get(winner), self.get(loser)
        p_win = expected_score(w.rating + winner_advantage, lo.rating)
        delta_w = self._k_for(winner) * mov_multiplier * (1.0 - p_win)
        delta_l = self._k_for(loser) * mov_multiplier * (0.0 - (1.0 - p_win))
        w.rating += delta_w
        lo.rating += delta_l
        w.matches += 1
        lo.matches += 1
        if when is not None:
            w.last_played = when
            lo.last_played = when
        return w.rating, lo.rating

    # --- persistence -----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            k: {
                "rating": v.rating,
                "matches": v.matches,
                "last_played": v.last_played.isoformat() if v.last_played else None,
            }
            for k, v in self.ratings.items()
        }

    def load_dict(self, data: dict) -> None:
        for k, v in data.items():
            self.ratings[k] = PlayerRating(
                rating=float(v["rating"]),
                matches=int(v.get("matches", 0)),
                last_played=datetime.fromisoformat(v["last_played"])
                if v.get("last_played")
                else None,
            )
