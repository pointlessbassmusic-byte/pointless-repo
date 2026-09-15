"""Tennis prediction: surface-blended Elo (FiveThirtyEight-style decaying K)
with a Markov-chain layer to translate between best-of-3 and best-of-5.

Prediction pipeline:
1. Overall Elo prob and surface-specific Elo prob, blended
   (default 70/30 overall/surface — surface samples are thinner).
2. The blended prob is treated as a best-of-3 match probability; for
   best-of-5 (slams) it is decomposed into serve-point probabilities via the
   Markov inversion and recomposed at best-of-5, which correctly widens
   favorites.
3. Uncertainty grows when either player has few rated matches or long
   inactivity.
"""

from __future__ import annotations

import json
import os

from sportsbot.core.elo import EloEngine, fte_tennis_k, prob_to_elo_diff
from sportsbot.core.markov import serve_probs_for_match_prob, tennis_match_win_prob
from sportsbot.core.types import Prediction, Sport
from sportsbot.data.tennis_data import MatchResult, normalize_player
from sportsbot.engine.base import EventInput, SportModel

SURFACE_KEYS = ("hard", "clay", "grass")


def _surface_key(surface: str) -> str:
    s = (surface or "").strip().lower()
    if s in ("carpet", "hard", "indoor hard"):
        return "hard"
    return s if s in SURFACE_KEYS else "hard"


class TennisModel(SportModel):
    name = "tennis_elo_markov_v1"
    sport = Sport.TENNIS

    def __init__(self, surface_weight: float = 0.50, min_matches: int = 10) -> None:
        self.surface_weight = surface_weight
        self.min_matches = min_matches
        self.overall = EloEngine(k_schedule=fte_tennis_k)
        self.by_surface: dict[str, EloEngine] = {
            k: EloEngine(k_schedule=fte_tennis_k) for k in SURFACE_KEYS
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _mov_multiplier(m: MatchResult) -> float:
        # WElo-inspired: blowouts move ratings more (0.5 + games_share stays
        # ~1.0 for tight wins, 1.5 for double bagels); retirements halve the
        # update (FiveThirtyEight convention).
        mult = 0.5 + max(0.5, min(1.0, m.games_share))
        if m.retirement:
            mult *= 0.5
        return mult

    def fit(self, history: list[MatchResult]) -> None:
        for m in history:
            mov = self._mov_multiplier(m)
            self.overall.update(m.winner, m.loser, when=m.date, mov_multiplier=mov)
            self.by_surface[_surface_key(m.surface)].update(
                m.winner, m.loser, when=m.date, mov_multiplier=mov
            )

    def update_result(self, winner: str, loser: str, surface: str, when=None) -> None:
        """Online update after a match settles."""
        winner, loser = normalize_player(winner), normalize_player(loser)
        self.overall.update(winner, loser, when=when)
        self.by_surface[_surface_key(surface)].update(winner, loser, when=when)

    # ------------------------------------------------------------------
    def predict(self, event: EventInput) -> Prediction:
        a = normalize_player(event.home)
        b = normalize_player(event.away)
        surface = _surface_key(str(event.context.get("surface", "hard")))

        p_overall = self.overall.predict(a, b)
        surf_engine = self.by_surface[surface]
        p_surface = surf_engine.predict(a, b)

        # Down-weight the surface signal when surface samples are thin.
        n_surf = min(surf_engine.get(a).matches, surf_engine.get(b).matches)
        w_surf = self.surface_weight * min(1.0, n_surf / 10.0)
        p_bo3 = (1.0 - w_surf) * p_overall + w_surf * p_surface

        best_of = int(event.context.get("best_of", event.best_of or 3))
        if best_of == 5:
            pa, pb = serve_probs_for_match_prob(p_bo3, best_of=3)
            prob = tennis_match_win_prob(pa, pb, best_of=5)
        else:
            prob = p_bo3

        n_a = self.overall.get(a).matches
        n_b = self.overall.get(b).matches
        n_min = min(n_a, n_b)
        # ~0.03 for veterans, up to 0.25 for unknowns.
        uncertainty = 0.03 + 0.22 * max(0.0, 1.0 - n_min / (2.0 * self.min_matches))

        return Prediction(
            market_id="",
            sport=Sport.TENNIS,
            model=self.name,
            prob_yes=prob,
            prob_raw=p_bo3,
            uncertainty=round(uncertainty, 4),
            features={
                "elo_a": round(self.overall.rating(a), 1),
                "elo_b": round(self.overall.rating(b), 1),
                "surface": surface,
                "elo_surface_a": round(surf_engine.rating(a), 1),
                "elo_surface_b": round(surf_engine.rating(b), 1),
                "matches_a": n_a,
                "matches_b": n_b,
                "best_of": best_of,
                "elo_diff": round(prob_to_elo_diff(prob), 1),
            },
        )

    # ------------------------------------------------------------------
    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        payload = {
            "overall": self.overall.to_dict(),
            "surfaces": {k: e.to_dict() for k, e in self.by_surface.items()},
        }
        with open(path, "w") as fh:
            json.dump(payload, fh)

    def load(self, path: str) -> None:
        with open(path) as fh:
            payload = json.load(fh)
        self.overall.load_dict(payload.get("overall", {}))
        for k, data in payload.get("surfaces", {}).items():
            if k in self.by_surface:
                self.by_surface[k].load_dict(data)
