"""Table tennis prediction: Elo tuned for high-frequency betting leagues
(Setka Cup, TT Cup, Czech Liga Pro) where players log dozens of matches per
week, plus a Markov set layer for best-of adjustments.

Design points from the research:
* K decays with rated matches: K = 40 / sqrt(1 + n/30), floored at 14 —
  converges fast on prolific league players.
* Rosters churn and motivation varies; uncertainty widens with inactivity.
* Match-fixing is endemic in these leagues — the strategy layer applies a
  higher minimum edge and a smaller cap for this sport; the model widens
  uncertainty rather than pretending precision.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

from sportsbot.core.elo import EloEngine
from sportsbot.core.markov import tt_match_win_prob, tt_point_prob_for_match_prob
from sportsbot.core.types import Prediction, Sport
from sportsbot.data.tabletennis_data import TTMatchResult, normalize_player
from sportsbot.engine.base import EventInput, SportModel


def tt_k_schedule(matches: int) -> float:
    return max(14.0, 40.0 / math.sqrt(1.0 + matches / 30.0))


class TableTennisModel(SportModel):
    name = "tt_elo_markov_v1"
    sport = Sport.TABLE_TENNIS

    def __init__(self, min_matches: int = 8) -> None:
        self.min_matches = min_matches
        self.elo = EloEngine(k_schedule=tt_k_schedule)

    def fit(self, history: list[TTMatchResult]) -> None:
        for m in history:
            self.elo.update(m.winner, m.loser, when=m.date)

    def update_result(self, winner: str, loser: str, when=None) -> None:
        self.elo.update(normalize_player(winner), normalize_player(loser), when=when)

    def predict(self, event: EventInput) -> Prediction:
        a = normalize_player(event.home)
        b = normalize_player(event.away)
        p_bo5 = self.elo.predict(a, b)

        best_of = int(event.context.get("best_of", event.best_of or 5))
        if best_of != 5:
            # Ratings are learned on (mostly) best-of-5 results; translate via
            # the point-level chain for best-of-7 finals etc.
            p_point = tt_point_prob_for_match_prob(p_bo5, best_of=5)
            prob = tt_match_win_prob(p_point, best_of=best_of)
        else:
            prob = p_bo5

        ra, rb = self.elo.get(a), self.elo.get(b)
        n_min = min(ra.matches, rb.matches)
        uncertainty = 0.05 + 0.25 * max(0.0, 1.0 - n_min / (2.0 * self.min_matches))
        # Widen for inactivity: league rosters churn hard.
        now = datetime.now(timezone.utc)
        for pr in (ra, rb):
            if pr.last_played is not None:
                idle_days = (now - pr.last_played).days
                if idle_days > 30:
                    uncertainty += min(0.10, 0.002 * (idle_days - 30))

        return Prediction(
            market_id="",
            sport=Sport.TABLE_TENNIS,
            model=self.name,
            prob_yes=prob,
            prob_raw=p_bo5,
            uncertainty=round(min(uncertainty, 0.35), 4),
            features={
                "elo_a": round(ra.rating, 1),
                "elo_b": round(rb.rating, 1),
                "matches_a": ra.matches,
                "matches_b": rb.matches,
                "best_of": best_of,
            },
        )

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as fh:
            json.dump({"elo": self.elo.to_dict()}, fh)

    def load(self, path: str) -> None:
        with open(path) as fh:
            self.elo.load_dict(json.load(fh).get("elo", {}))
