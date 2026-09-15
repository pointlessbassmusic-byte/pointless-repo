import math

import pytest

from sportsbot.core.calibration import blend_with_market, brier_score, log_loss
from sportsbot.core.elo import EloEngine, expected_score, fte_tennis_k, prob_to_elo_diff
from sportsbot.core.markov import (
    best_of_win_prob,
    game_win_prob,
    serve_probs_for_match_prob,
    set_win_prob,
    tennis_match_win_prob,
    tiebreak_win_prob,
    tt_match_win_prob,
    tt_point_prob_for_match_prob,
    tt_set_win_prob,
)
from sportsbot.core.odds import (
    american_to_prob,
    cents_to_prob,
    prob_to_cents,
    remove_vig_two_way,
)
from sportsbot.core.staking import StakingConfig, decide_stake, kelly_binary


class TestMarkov:
    def test_game_symmetry_and_known_value(self):
        assert game_win_prob(0.5) == pytest.approx(0.5)
        # O'Malley closed form at p=0.6 is ~0.7357
        assert game_win_prob(0.6) == pytest.approx(0.7357, abs=1e-3)
        assert game_win_prob(1.0) == 1.0
        assert game_win_prob(0.0) == 0.0

    def test_monotonicity(self):
        probs = [game_win_prob(p / 100) for p in range(30, 90, 5)]
        assert probs == sorted(probs)

    def test_tiebreak_symmetric(self):
        assert tiebreak_win_prob(0.62, 0.62) == pytest.approx(0.5, abs=1e-9)
        assert tiebreak_win_prob(0.7, 0.6) > 0.5

    def test_set_complementarity(self):
        s1 = set_win_prob(0.64, 0.60, a_serves_first=True)
        s2 = set_win_prob(0.60, 0.64, a_serves_first=False)
        assert s1 == pytest.approx(1.0 - (1.0 - s1), abs=1e-12)
        assert s1 + s2 == pytest.approx(1.0, abs=1e-9)  # role swap symmetry

    def test_match_bo5_widens_favorite(self):
        p3 = tennis_match_win_prob(0.66, 0.60, best_of=3)
        p5 = tennis_match_win_prob(0.66, 0.60, best_of=5)
        assert p5 > p3 > 0.5

    def test_best_of_closed_form(self):
        p = 0.6
        expected_bo3 = p * p * (1 + 2 * (1 - p))
        assert best_of_win_prob(p, 3) == pytest.approx(expected_bo3, abs=1e-12)

    def test_serve_prob_inversion_roundtrip(self):
        pa, pb = serve_probs_for_match_prob(0.72, best_of=3)
        assert tennis_match_win_prob(pa, pb, 3) == pytest.approx(0.72, abs=1e-4)

    def test_tt_roundtrip(self):
        assert tt_set_win_prob(0.5) == pytest.approx(0.5)
        assert tt_match_win_prob(0.55, 5) > tt_set_win_prob(0.55) > 0.55
        p = tt_point_prob_for_match_prob(0.65, best_of=5)
        assert tt_match_win_prob(p, 5) == pytest.approx(0.65, abs=1e-4)


class TestElo:
    def test_expected_and_inverse(self):
        assert expected_score(1500, 1500) == 0.5
        d = prob_to_elo_diff(0.75)
        assert expected_score(1500 + d, 1500) == pytest.approx(0.75, abs=1e-9)

    def test_update_moves_ratings(self):
        e = EloEngine(k_factor=32)
        e.update("a", "b")
        assert e.rating("a") > 1500 > e.rating("b")
        # zero-sum with equal K
        assert e.rating("a") + e.rating("b") == pytest.approx(3000, abs=1e-9)

    def test_home_advantage_not_double_counted(self):
        e = EloEngine(k_factor=32)
        # Winner had a big contextual advantage -> smaller skill update.
        e2 = EloEngine(k_factor=32)
        e.update("a", "b", winner_advantage=100.0)
        e2.update("a", "b", winner_advantage=0.0)
        assert e.rating("a") < e2.rating("a")

    def test_fte_k_decays(self):
        assert fte_tennis_k(0) > fte_tennis_k(20) > fte_tennis_k(200)

    def test_serialization_roundtrip(self):
        e = EloEngine()
        e.update("x", "y")
        e2 = EloEngine()
        e2.load_dict(e.to_dict())
        assert e2.rating("x") == pytest.approx(e.rating("x"))
        assert e2.get("x").matches == 1


class TestOdds:
    def test_american(self):
        assert american_to_prob(-150) == pytest.approx(0.6, abs=1e-9)
        assert american_to_prob(150) == pytest.approx(0.4, abs=1e-9)

    def test_vig_removal(self):
        a, b = remove_vig_two_way(0.55, 0.55)
        assert a == pytest.approx(0.5)
        assert a + b == pytest.approx(1.0)

    def test_cents(self):
        assert cents_to_prob(65) == 0.65
        assert prob_to_cents(0.654) == 65
        assert prob_to_cents(0.001) == 1
        assert prob_to_cents(0.999) == 99


class TestStaking:
    def test_kelly_formula(self):
        # q=0.6 at p=0.5: f* = 0.1/0.5 = 0.2
        assert kelly_binary(0.6, 0.5) == pytest.approx(0.2)
        assert kelly_binary(0.5, 0.5) == 0.0
        assert kelly_binary(0.4, 0.5) == 0.0

    def test_min_edge_gate(self):
        cfg = StakingConfig(bankroll=1000, min_edge=0.03)
        assert not decide_stake(0.52, 0.50, cfg).approved
        assert decide_stake(0.56, 0.50, cfg).approved

    def test_caps_respected(self):
        cfg = StakingConfig(bankroll=10000, max_stake_per_market=50)
        d = decide_stake(0.7, 0.5, cfg)
        assert d.approved and d.stake <= 50

    def test_exposure_exhaustion(self):
        cfg = StakingConfig(bankroll=1000)
        d = decide_stake(0.7, 0.5, cfg, current_total_exposure=500.0)
        assert not d.approved  # total cap 50% already used

    def test_min_stake_gate(self):
        cfg = StakingConfig(bankroll=100, min_stake=5.0, kelly_multiplier=0.05)
        d = decide_stake(0.54, 0.50, cfg, )
        assert not d.approved


class TestCalibration:
    def test_scores(self):
        assert brier_score([1.0, 0.0], [1, 0]) == 0.0
        assert log_loss([0.5, 0.5], [1, 0]) == pytest.approx(math.log(2))

    def test_blend(self):
        assert blend_with_market(0.6, 0.5, 0.3) == pytest.approx(0.53)
        assert blend_with_market(0.6, None, 0.3) == 0.6
