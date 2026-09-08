"""Walk-forward backtesting: train on the past only, predict each match as it
arrives, update afterwards. Reports log loss / Brier / accuracy against the
realistic benchmarks from the literature:

  tennis Elo target:  ~0.60-0.63 log loss, 64-66% accuracy (all-ATP)
  MLB target:         ~0.66-0.68 log loss (always-home baseline 0.689)
  bookmaker close:    the ceiling — don't expect to beat it on headline lines

Side order is randomized per match (seeded) so the evaluated probability is
not always the winner's — otherwise log loss is computed on a label-leaked
sample.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sportsbot.core.calibration import brier_score, calibration_bins, log_loss
from sportsbot.core.types import Sport
from sportsbot.data.tennis_data import MatchResult
from sportsbot.data.tabletennis_data import TTMatchResult
from sportsbot.engine.base import EventInput
from sportsbot.engine.tabletennis import TableTennisModel
from sportsbot.engine.tennis import TennisModel


@dataclass
class BacktestReport:
    n: int = 0
    log_loss: float = 0.0
    brier: float = 0.0
    accuracy: float = 0.0
    baseline_log_loss: float = 0.6931
    bins: list = field(default_factory=list)

    def summary(self) -> str:
        return (f"n={self.n} log_loss={self.log_loss:.4f} "
                f"(coin flip {self.baseline_log_loss:.4f}) "
                f"brier={self.brier:.4f} accuracy={self.accuracy:.2%}")


def _score(probs: list[float], outcomes: list[int]) -> BacktestReport:
    rpt = BacktestReport(
        n=len(probs),
        log_loss=log_loss(probs, outcomes),
        brier=brier_score(probs, outcomes),
        accuracy=sum(1 for p, o in zip(probs, outcomes)
                     if (p >= 0.5) == (o == 1)) / len(probs),
        bins=calibration_bins(probs, outcomes),
    )
    return rpt


def walk_forward_tennis(matches: list[MatchResult], warmup: int = 2000,
                        seed: int = 7, surface_weight: float = 0.5) -> BacktestReport:
    """Chronological walk-forward over Sackmann match results."""
    model = TennisModel(surface_weight=surface_weight)
    rng = random.Random(seed)
    probs: list[float] = []
    outcomes: list[int] = []
    for i, m in enumerate(matches):
        if i >= warmup and not m.retirement:
            if rng.random() < 0.5:
                a, b, won = m.winner, m.loser, 1
            else:
                a, b, won = m.loser, m.winner, 0
            pred = model.predict(EventInput(
                sport=Sport.TENNIS, home=a, away=b, best_of=m.best_of,
                context={"surface": m.surface, "best_of": m.best_of},
            ))
            if pred.uncertainty <= 0.20:  # mirror live filter
                probs.append(pred.prob_yes)
                outcomes.append(won)
        model.fit([m])
    if not probs:
        raise ValueError("no evaluated matches — increase data or lower warmup")
    return _score(probs, outcomes)


def walk_forward_mlb(games, warmup: int = 500) -> BacktestReport:
    """Chronological walk-forward over MLB GameResults. Home side is fixed by
    the schedule, so no side randomization is needed (no label leak: the
    predicted side is 'home', not 'winner')."""
    from sportsbot.data.mlb_data import GameResult  # noqa: F401
    from sportsbot.engine.baseball import BaseballModel

    model = BaseballModel()
    probs: list[float] = []
    outcomes: list[int] = []
    for i, g in enumerate(games):
        if i >= warmup:
            pred = model.predict(EventInput(
                sport=Sport.BASEBALL, home=g.home, away=g.away,
                context={"home_sp": g.home_sp, "away_sp": g.away_sp},
            ))
            probs.append(pred.prob_yes)
            outcomes.append(1 if g.home_won else 0)
        model.update_result(g)
    if not probs:
        raise ValueError("no evaluated games — increase data or lower warmup")
    rpt = _score(probs, outcomes)
    rpt.baseline_log_loss = 0.689  # always-predict-54%-home baseline
    return rpt


def walk_forward_tt(matches: list[TTMatchResult], warmup: int = 500,
                    seed: int = 7) -> BacktestReport:
    model = TableTennisModel()
    rng = random.Random(seed)
    probs: list[float] = []
    outcomes: list[int] = []
    for i, m in enumerate(matches):
        if i >= warmup:
            if rng.random() < 0.5:
                a, b, won = m.winner, m.loser, 1
            else:
                a, b, won = m.loser, m.winner, 0
            pred = model.predict(EventInput(
                sport=Sport.TABLE_TENNIS, home=a, away=b, best_of=m.best_of,
                context={"best_of": m.best_of},
            ))
            if pred.uncertainty <= 0.20:
                probs.append(pred.prob_yes)
                outcomes.append(won)
        model.fit([m])
    if not probs:
        raise ValueError("no evaluated matches — increase data or lower warmup")
    return _score(probs, outcomes)
