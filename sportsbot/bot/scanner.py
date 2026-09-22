"""Scanner: venue markets -> matched, predicted opportunities.

For each active moneyline market: resolve both participants against the
model's rated entities (conservative fuzzy matching — unmatched means skip),
build an EventInput, and predict. Markets are predicted in the market's own
frame: prob_yes = P(outcomes[0] / MarketInfo.home wins).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sportsbot.bot.matching import match_entity
from sportsbot.core.types import MarketInfo, Prediction, Sport
from sportsbot.engine.base import EventInput, SportModel
from sportsbot.engine.baseball import BaseballModel
from sportsbot.engine.tabletennis import TableTennisModel
from sportsbot.engine.tennis import TennisModel

log = logging.getLogger(__name__)


@dataclass
class ScannedMarket:
    market: MarketInfo
    prediction: Prediction
    matched_home: str
    matched_away: str


class Scanner:
    def __init__(self, models: dict[Sport, SportModel],
                 match_threshold: float = 0.85) -> None:
        self.models = models
        self.match_threshold = match_threshold

    def _rated_entities(self, model: SportModel) -> list[str]:
        if isinstance(model, TennisModel):
            return list(model.overall.ratings.keys())
        if isinstance(model, BaseballModel):
            return list(model.elo.ratings.keys())
        if isinstance(model, TableTennisModel):
            return list(model.elo.ratings.keys())
        return []

    def scan(self, markets: list[MarketInfo],
             extra_context: Optional[dict] = None) -> list[ScannedMarket]:
        """`extra_context` maps market_id -> context dict (e.g. probable
        pitchers keyed in by the runner from MLB Stats API)."""
        out: list[ScannedMarket] = []
        for m in markets:
            if m.sport is None or m.sport not in self.models:
                continue
            if not m.home or not m.away:
                continue
            model = self.models[m.sport]
            candidates = self._rated_entities(model)
            if not candidates:
                log.warning("model %s has no rated entities; skipping scan", model.name)
                continue
            home = match_entity(m.home, candidates, self.match_threshold)
            away = match_entity(m.away, candidates, self.match_threshold)
            if home is None or away is None or home == away:
                continue
            context = dict((extra_context or {}).get(m.market_id, {}))

            # `MarketInfo.home` is the side the YES contract pays on, which is
            # not always the team with home-field advantage: Kalshi lists one
            # market per team, so half of them have the visitor as the YES
            # side. Model the real matchup, then restate the answer for the
            # YES side so `prob_yes = P(MarketInfo.home wins)` still holds.
            home_field = (m.meta or {}).get("home_field")
            yes_is_home = True
            if home_field:
                matched_field = match_entity(str(home_field), candidates,
                                             self.match_threshold)
                if matched_field is not None and matched_field == away:
                    yes_is_home = False

            event = EventInput(
                sport=m.sport,
                home=home if yes_is_home else away,
                away=away if yes_is_home else home,
                start_time=m.start_time,
                best_of=int(context.get("best_of", 3 if m.sport == Sport.TENNIS else 5)),
                context=context,
            )
            try:
                pred = model.predict(event)
            except Exception:
                log.exception("prediction failed for %s", m.market_id)
                continue
            if not yes_is_home:      # restate for the YES side
                pred.prob_yes = 1.0 - pred.prob_yes
                if pred.prob_raw is not None:
                    pred.prob_raw = 1.0 - pred.prob_raw
            pred.market_id = m.market_id
            out.append(ScannedMarket(market=m, prediction=pred,
                                     matched_home=home, matched_away=away))
        return out
