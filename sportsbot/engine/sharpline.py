"""The sharp line as a model: P(home) = Pinnacle's Shin-de-vigged fair.

Every edge that survived fees in the literature was measured against a
sharp close, and this repo's own ratings carry no information the price
lacks (Results 2–9). So the "model" the research report ranks first is
not a rating at all: it is the sharpest external price, read at decision
time from the harness's own store (`signals/sharp.py` collects it).

The scanner treats this like any SportModel. Its rated entities are the
participants in the sharp quotes on record, so unmatched markets are
skipped by the same conservative matcher as before. When no quote exists
for an event the prediction is returned with `uncertainty` 1.0 and the
strategy skips it with that reason -- there is no fallback to a rating,
because a rating is exactly what the evidence says not to trade on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sportsbot.core.types import Prediction, Sport
from sportsbot.engine.base import EventInput, SportModel
from sportsbot.signals.sharp import SharpConfig, match_event, quote_at

NAME = "sharpline"


class SharpLineModel(SportModel):
    name = NAME

    def __init__(self, store, sport: Sport, cfg: Optional[SharpConfig] = None,
                 max_age_minutes: float = 120.0) -> None:
        self.store = store
        self.sport = sport
        self.cfg = cfg or SharpConfig()
        self.max_age_minutes = max_age_minutes
        self._events: Optional[list[dict]] = None

    # -- SportModel contract ---------------------------------------------
    def fit(self, history: Any) -> None:   # nothing to fit: the line is the model
        return None

    def save(self, path: str) -> None:
        return None

    def load(self, path: str) -> None:
        return None

    # -- the sharp book on record ------------------------------------------
    def refresh(self) -> None:
        self._events = None

    def events(self) -> list[dict]:
        if self._events is None:
            self._events = self.store.sharp_events(self.cfg.sharp_book)
        return self._events

    def rated_entities(self) -> list[str]:
        names: set[str] = set()
        for ev in self.events():
            names.add(ev["home_team"])
            names.add(ev["away_team"])
        return sorted(names)

    def fair_for(self, home: str, away: str, start_time: Optional[datetime],
                 now: Optional[datetime] = None) -> Optional[tuple[float, dict]]:
        """(P(home), quote) from the latest sharp quote no older than
        `max_age_minutes`, or None."""
        now = now or datetime.now(timezone.utc)
        hit = match_event({"home": home, "away": away, "start_time": start_time},
                          self.events(), self.cfg.match_threshold,
                          self.cfg.time_slack_hours)
        if hit is None:
            return None
        ev, yes_is_home = hit
        q = quote_at(self.store.sharp_quotes_for(ev["event_id"], self.cfg.sharp_book), now)
        if q is None:
            return None
        ts = datetime.fromisoformat(str(q["ts"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if (now - ts).total_seconds() > self.max_age_minutes * 60.0:
            return None
        p = float(q["home_fair"] if yes_is_home else q["away_fair"])
        return p, q

    def predict(self, event: EventInput) -> Prediction:
        got = self.fair_for(event.home, event.away, event.start_time)
        if got is None:
            return Prediction(market_id="", sport=self.sport, model=self.name,
                              prob_yes=0.5, prob_raw=None, uncertainty=1.0,
                              features={"sharp": "no fresh line on record"})
        p, q = got
        return Prediction(market_id="", sport=self.sport, model=self.name,
                          prob_yes=p, prob_raw=p, uncertainty=0.0,
                          features={"sharp_book": self.cfg.sharp_book,
                                    "sharp_ts": q["ts"],
                                    "overround": q.get("overround")})
