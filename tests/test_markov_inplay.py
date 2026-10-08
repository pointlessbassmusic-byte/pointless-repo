"""In-play Markov fair value: consistency with the pre-match chain and an
independent Monte Carlo of full tennis scoring from mid-match states."""
import random

import pytest

from sportsbot.core.markov import (tennis_match_win_prob, tennis_point_leverage,
                                   tennis_win_prob_from_state, tt_match_win_prob,
                                   tt_win_prob_from_state)


def _sim_tennis(rng, p_a, p_b, sa, sb, ga, gb, pa, pb, server_a, best_of=3, final_tb=7):
    """Point-by-point simulation with standard serve rotation."""
    need = best_of // 2 + 1
    tb_first = server_a
    while True:
        if sa >= need:
            return 1
        if sb >= need:
            return 0
        if ga == 6 and gb == 6:                       # tiebreak
            target = final_tb if sa + sb == best_of - 1 else 7
            while True:
                k = pa + pb + 1
                a_srv = ((k % 4) in (1, 0)) == tb_first
                pw = p_a if a_srv else 1 - p_b
                if rng.random() < pw:
                    pa += 1
                else:
                    pb += 1
                if pa >= target and pa - pb >= 2:
                    sa += 1
                    break
                if pb >= target and pb - pa >= 2:
                    sb += 1
                    break
            ga = gb = pa = pb = 0
            server_a = not tb_first
            tb_first = server_a
            continue
        pw = p_a if server_a else 1 - p_b             # P(A wins the point)
        if rng.random() < pw:
            pa += 1
        else:
            pb += 1
        if (pa >= 4 and pa - pb >= 2) or (pb >= 4 and pb - pa >= 2):
            if pa > pb:
                ga += 1
            else:
                gb += 1
            pa = pb = 0
            if (ga >= 6 and ga - gb >= 2) or (gb >= 6 and gb - ga >= 2):
                if ga > gb:
                    sa += 1
                else:
                    sb += 1
                ga = gb = 0
            server_a = not server_a
            tb_first = server_a


def test_from_start_matches_pre_match_chain():
    for pa, pb in ((0.64, 0.64), (0.66, 0.60), (0.58, 0.70)):
        avg = (tennis_win_prob_from_state(pa, pb, server_a=True)
               + tennis_win_prob_from_state(pa, pb, server_a=False)) / 2
        assert avg == pytest.approx(tennis_match_win_prob(pa, pb), abs=1e-6)
    assert tt_win_prob_from_state(0.53) == pytest.approx(tt_match_win_prob(0.53), abs=1e-9)


@pytest.mark.parametrize("state", [
    dict(sets_a=1, sets_b=0, games_a=4, games_b=5, pts_a=2, pts_b=3, server_a=False),
    dict(sets_a=0, sets_b=1, games_a=6, games_b=6, pts_a=4, pts_b=5, server_a=True),
    dict(sets_a=1, sets_b=1, games_a=3, games_b=3, pts_a=3, pts_b=3, server_a=True),
])
def test_mid_match_states_agree_with_monte_carlo(state):
    p_a, p_b, n = 0.65, 0.61, 30000
    exact = tennis_win_prob_from_state(p_a, p_b, **state)
    rng = random.Random(7)
    wins = sum(_sim_tennis(rng, p_a, p_b, state["sets_a"], state["sets_b"], state["games_a"],
                           state["games_b"], state["pts_a"], state["pts_b"], state["server_a"])
               for _ in range(n))
    se = (exact * (1 - exact) / n) ** 0.5
    assert abs(wins / n - exact) < 4 * se + 1e-9


def test_terminal_and_leverage_shape():
    assert tennis_win_prob_from_state(0.6, 0.6, sets_a=2) == 1.0
    assert tennis_win_prob_from_state(0.6, 0.6, sets_b=2) == 0.0
    big = tennis_point_leverage(0.64, 0.64, sets_a=1, sets_b=1, games_a=4, games_b=4,
                                pts_a=3, pts_b=3, server_a=True)
    small = tennis_point_leverage(0.64, 0.64, games_a=5, games_b=0, pts_a=3, pts_b=0,
                                  server_a=True)
    assert big > 0.15 and small < 0.01
    # deciding-set tiebreak at 6-6 (target 7) is nearly a coin flip on the next point
    assert tennis_point_leverage(0.64, 0.64, sets_a=1, sets_b=1, games_a=6, games_b=6,
                                 pts_a=6, pts_b=6, server_a=True) > 0.4


def test_tt_in_play():
    assert tt_win_prob_from_state(0.5, sets_a=2, sets_b=2, pts_a=10, pts_b=9) == pytest.approx(0.75)
    assert tt_win_prob_from_state(0.5, sets_a=3) == 1.0
