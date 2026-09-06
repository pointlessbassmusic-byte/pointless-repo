"""MLB game prediction: team Elo (FiveThirtyEight-style) with home advantage,
margin-of-victory scaling, season mean-reversion, and a starting-pitcher
adjustment learned online from game outcomes.

Baseball is a low-K sport: single games are ~coin flips (best teams win ~60%),
so ratings move slowly (K≈4) and the market blend weight matters more than in
tennis. Home advantage ≈ 24 Elo points (~54% home win rate).
"""

from __future__ import annotations

import json
import math
import os
from typing import Optional

from sportsbot.core.elo import EloEngine, expected_score
from sportsbot.core.types import Prediction, Sport
from sportsbot.data.mlb_data import GameResult, normalize_team
from sportsbot.engine.base import EventInput, SportModel


class PitcherBook:
    """Per-pitcher Elo-point adjustment, learned from starts.

    After each start: adj += k * (outcome - expected) * scale, capped. A hot
    starter drifts positive; expected uses the team-level prediction so the
    pitcher only absorbs residual signal.
    """

    def __init__(self, k: float = 8.0, cap: float = 60.0) -> None:
        self.k = k
        self.cap = cap
        self.adj: dict[str, float] = {}
        self.starts: dict[str, int] = {}

    def get(self, name: Optional[str]) -> float:
        if not name:
            return 0.0
        return self.adj.get(name, 0.0)

    def update(self, name: Optional[str], outcome: float, expected: float) -> None:
        if not name:
            return
        cur = self.adj.get(name, 0.0)
        cur += self.k * (outcome - expected) * 4.0  # 4 Elo pts per full residual win
        self.adj[name] = max(-self.cap, min(self.cap, cur))
        self.starts[name] = self.starts.get(name, 0) + 1


class BaseballModel(SportModel):
    name = "mlb_elo_sp_v1"
    sport = Sport.BASEBALL

    def __init__(
        self,
        k_factor: float = 4.0,
        home_advantage: float = 24.0,
        rest_per_day: float = 2.3,
        sp_enabled: bool = True,
        season_reversion: float = 1.0 / 3.0,
    ) -> None:
        self.elo = EloEngine(k_factor=k_factor)
        self.home_advantage = home_advantage
        self.rest_per_day = rest_per_day
        self.sp_enabled = sp_enabled
        self.season_reversion = season_reversion
        self.pitchers = PitcherBook()
        self._last_season: Optional[int] = None

    # ------------------------------------------------------------------
    @staticmethod
    def _mov_multiplier(margin: int, elo_diff_winner: float) -> float:
        """FiveThirtyEight-style margin-of-victory damping: blowouts move
        ratings more, but less so for heavy favorites (autocorrelation fix).
        """
        margin = max(1, abs(margin))
        return math.log(margin + 1.0) * (2.2 / (elo_diff_winner * 0.001 + 2.2))

    def _maybe_revert(self, season: int) -> None:
        if self._last_season is not None and season != self._last_season:
            for pr in self.elo.ratings.values():
                pr.rating += self.season_reversion * (1500.0 - pr.rating)
        self._last_season = season

    def fit(self, history: list[GameResult]) -> None:
        for g in history:
            self.update_result(g)

    def update_result(self, g: GameResult) -> None:
        self._maybe_revert(g.date.year)
        home, away = normalize_team(g.home), normalize_team(g.away)
        p_home = self._predict_probability(home, away, g.home_sp, g.away_sp)

        winner, loser = (home, away) if g.home_won else (away, home)
        winner_adv = self.home_advantage if g.home_won else -self.home_advantage
        elo_diff_winner = self.elo.rating(winner) + winner_adv - self.elo.rating(loser)
        mov = self._mov_multiplier(g.home_score - g.away_score, elo_diff_winner)
        self.elo.update(winner, loser, when=g.date, winner_advantage=winner_adv,
                        mov_multiplier=mov)

        if self.sp_enabled:
            self.pitchers.update(g.home_sp, 1.0 if g.home_won else 0.0, p_home)
            self.pitchers.update(g.away_sp, 0.0 if g.home_won else 1.0, 1.0 - p_home)

    # ------------------------------------------------------------------
    def _predict_probability(self, home: str, away: str,
                             home_sp: Optional[str], away_sp: Optional[str],
                             rest_diff_days: float = 0.0) -> float:
        adv = self.home_advantage
        if self.sp_enabled:
            adv += self.pitchers.get(home_sp) - self.pitchers.get(away_sp)
        adv += max(-3.0, min(3.0, rest_diff_days)) * self.rest_per_day
        return expected_score(self.elo.rating(home) + adv, self.elo.rating(away))

    def predict(self, event: EventInput) -> Prediction:
        home = normalize_team(event.home)
        away = normalize_team(event.away)
        home_sp = event.context.get("home_sp")
        away_sp = event.context.get("away_sp")
        rest_diff = float(event.context.get("rest_diff_days", 0.0))
        prob = self._predict_probability(home, away, home_sp, away_sp, rest_diff)

        n_min = min(self.elo.get(home).matches, self.elo.get(away).matches)
        uncertainty = 0.03 + 0.15 * max(0.0, 1.0 - n_min / 100.0)
        if self.sp_enabled and (not home_sp or not away_sp):
            uncertainty += 0.02  # probable pitcher unknown

        return Prediction(
            market_id="",
            sport=Sport.BASEBALL,
            model=self.name,
            prob_yes=prob,
            prob_raw=prob,
            uncertainty=round(uncertainty, 4),
            features={
                "elo_home": round(self.elo.rating(home), 1),
                "elo_away": round(self.elo.rating(away), 1),
                "home_sp": home_sp,
                "away_sp": away_sp,
                "sp_adj_home": round(self.pitchers.get(home_sp), 1),
                "sp_adj_away": round(self.pitchers.get(away_sp), 1),
                "games_home": self.elo.get(home).matches,
                "games_away": self.elo.get(away).matches,
            },
        )

    # ------------------------------------------------------------------
    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as fh:
            json.dump({
                "elo": self.elo.to_dict(),
                "pitchers": self.pitchers.adj,
                "pitcher_starts": self.pitchers.starts,
                "last_season": self._last_season,
            }, fh)

    def load(self, path: str) -> None:
        with open(path) as fh:
            payload = json.load(fh)
        self.elo.load_dict(payload.get("elo", {}))
        self.pitchers.adj = {k: float(v) for k, v in payload.get("pitchers", {}).items()}
        self.pitchers.starts = {k: int(v) for k, v in payload.get("pitcher_starts", {}).items()}
        self._last_season = payload.get("last_season")
