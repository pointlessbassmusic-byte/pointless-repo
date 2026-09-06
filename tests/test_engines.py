from datetime import datetime, timedelta, timezone

import pytest

from sportsbot.core.types import Sport
from sportsbot.data.mlb_data import GameResult
from sportsbot.data.tabletennis_data import TTMatchResult
from sportsbot.data.tennis_data import MatchResult, parse_score
from sportsbot.engine.base import EventInput
from sportsbot.engine.baseball import BaseballModel
from sportsbot.engine.tabletennis import TableTennisModel
from sportsbot.engine.tennis import TennisModel

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _tennis_history(n=40):
    # "alpha" beats "beta" consistently on hard courts
    return [
        MatchResult(date=NOW - timedelta(days=n - i), winner="alpha", loser="beta",
                    surface="Hard", best_of=3, level="A", tourney="t",
                    games_share=0.6)
        for i in range(n)
    ]


class TestTennisModel:
    def test_learns_dominance(self):
        m = TennisModel()
        m.fit(_tennis_history())
        pred = m.predict(EventInput(sport=Sport.TENNIS, home="alpha", away="beta",
                                    context={"surface": "Hard"}))
        assert pred.prob_yes > 0.75
        # symmetric query
        pred_rev = m.predict(EventInput(sport=Sport.TENNIS, home="beta", away="alpha",
                                        context={"surface": "Hard"}))
        assert pred_rev.prob_yes == pytest.approx(1.0 - pred.prob_yes, abs=1e-6)

    def test_bo5_widens(self):
        m = TennisModel()
        m.fit(_tennis_history())
        p3 = m.predict(EventInput(sport=Sport.TENNIS, home="alpha", away="beta",
                                  context={"surface": "Hard", "best_of": 3})).prob_yes
        p5 = m.predict(EventInput(sport=Sport.TENNIS, home="alpha", away="beta",
                                  context={"surface": "Hard", "best_of": 5})).prob_yes
        assert p5 > p3

    def test_unknown_player_high_uncertainty(self):
        m = TennisModel()
        m.fit(_tennis_history())
        pred = m.predict(EventInput(sport=Sport.TENNIS, home="alpha", away="nobody",
                                    context={"surface": "Hard"}))
        assert pred.uncertainty > 0.2

    def test_save_load_roundtrip(self, tmp_path):
        m = TennisModel()
        m.fit(_tennis_history())
        path = str(tmp_path / "tennis.json")
        m.save(path)
        m2 = TennisModel()
        m2.load(path)
        assert m2.overall.rating("alpha") == pytest.approx(m.overall.rating("alpha"))

    def test_parse_score(self):
        share, ret = parse_score("6-4 3-6 7-6(5)")
        assert share == pytest.approx(16 / 32)
        assert not ret
        share, ret = parse_score("6-2 3-1 RET")
        assert ret
        share, _ = parse_score("7-6(3) 6-7(5) 10-8")  # match tiebreak counts as one game
        assert 0.4 < share < 0.7


class TestBaseballModel:
    def _history(self, n=60):
        games = []
        for i in range(n):
            # yankees beat mets most days, alternating home/away
            home, away = (("yankees", "mets") if i % 2 == 0 else ("mets", "yankees"))
            hw = home == "yankees"
            games.append(GameResult(
                date=NOW - timedelta(days=n - i), home=home, away=away,
                home_score=5 if hw else 2, away_score=2 if hw else 5,
                home_sp="ace" if home == "yankees" else "scrub",
                away_sp="scrub" if home == "yankees" else "ace",
            ))
        return games

    def test_learns_team_strength(self):
        m = BaseballModel()
        m.fit(self._history())
        pred = m.predict(EventInput(sport=Sport.BASEBALL, home="yankees", away="mets"))
        assert pred.prob_yes > 0.55

    def test_home_advantage(self):
        m = BaseballModel()
        p_home = m.predict(EventInput(sport=Sport.BASEBALL, home="a", away="b")).prob_yes
        assert p_home > 0.5  # equal teams, home edge only

    def test_sp_adjustment_direction(self):
        m = BaseballModel()
        m.fit(self._history())
        with_ace = m.predict(EventInput(sport=Sport.BASEBALL, home="yankees", away="mets",
                                        context={"home_sp": "ace", "away_sp": "scrub"}))
        without = m.predict(EventInput(sport=Sport.BASEBALL, home="yankees", away="mets"))
        assert with_ace.prob_yes >= without.prob_yes

    def test_save_load(self, tmp_path):
        m = BaseballModel()
        m.fit(self._history(20))
        path = str(tmp_path / "mlb.json")
        m.save(path)
        m2 = BaseballModel()
        m2.load(path)
        assert m2.elo.rating("yankees") == pytest.approx(m.elo.rating("yankees"))


class TestTableTennisModel:
    def test_learns_and_widens_uncertainty(self):
        m = TableTennisModel()
        hist = [
            TTMatchResult(date=NOW - timedelta(days=50 - i), winner="ivan", loser="petro")
            for i in range(40)
        ]
        m.fit(hist)
        pred = m.predict(EventInput(sport=Sport.TABLE_TENNIS, home="ivan", away="petro"))
        assert pred.prob_yes > 0.7
        stranger = m.predict(EventInput(sport=Sport.TABLE_TENNIS, home="ivan", away="ghost"))
        assert stranger.uncertainty > pred.uncertainty

    def test_bo7_translation(self):
        m = TableTennisModel()
        hist = [
            TTMatchResult(date=NOW - timedelta(days=30 - i), winner="a", loser="b")
            for i in range(30)
        ]
        m.fit(hist)
        p5 = m.predict(EventInput(sport=Sport.TABLE_TENNIS, home="a", away="b",
                                  context={"best_of": 5})).prob_yes
        p7 = m.predict(EventInput(sport=Sport.TABLE_TENNIS, home="a", away="b",
                                  context={"best_of": 7})).prob_yes
        assert p7 > p5 > 0.5
