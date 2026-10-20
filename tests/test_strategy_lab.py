"""strategy_lab: each test pins a way a strategy backtest can flatter itself."""

from sportsbot.backtest.strategy_lab import (
    Outcome,
    endgame,
    follow_and_fade,
    inversion_gap,
    kalshi_taker,
    overreaction,
    sibling_lock_cost,
    toward_other_venue,
)

FEE = kalshi_taker(1.0)
NOFEE = lambda p: 0.0  # noqa: E731


def test_follow_plus_fade_is_minus_the_round_trip_not_zero():
    """The user's question, pinned: the opposite of a losing trade is not a
    winner, because both pay the toll. With no information in the model the
    two sides sum to minus (2 x slippage + fees)."""
    games = [(0.60, 0.50, i % 2 == 0) for i in range(200)]   # model says YES, coin-flip truth
    fol, fad = follow_and_fade(games, 0.05, FEE, slip=0.005)
    gap = inversion_gap(fol, fad)
    assert gap < 0
    assert abs(gap - (-2 * 0.005 - 2 * FEE(0.505))) < 1e-9
    assert fol.mean < 0 and fad.mean < 0          # BOTH lose


def test_fade_wins_only_when_the_signal_is_anti_predictive():
    games = [(0.70, 0.50, False)] * 50               # model always wrong
    fol, fad = follow_and_fade(games, 0.05, NOFEE, slip=0.0)
    assert fol.mean < 0 < fad.mean


def test_sibling_arb_requires_beating_fees_not_just_one():
    assert sibling_lock_cost(0.49, 0.49, 0.48, 0.48, NOFEE) < 1.0      # pre-fee arb
    assert sibling_lock_cost(0.495, 0.495, 0.48, 0.48, FEE) > 1.0      # fees kill it
    # the NO leg: bids summing above 1 lock $1 by buying both NOs
    assert sibling_lock_cost(0.60, 0.60, 0.55, 0.55, NOFEE) < 1.0


def test_endgame_buys_the_leader_once_per_event():
    tape = [(0, 0.80), (1, 0.93), (2, 0.96), (3, 0.99)]
    o = endgame([(tape, 0, True)], 0.95, NOFEE, slip=0.0)
    assert o.n == 1 and abs(o.pnls[0] - (1 - 0.96)) < 1e-9


def test_endgame_takes_the_no_side_for_a_collapsing_price():
    tape = [(0, 0.20), (1, 0.03)]
    o = endgame([(tape, 0, False)], 0.95, NOFEE, slip=0.0)
    assert o.n == 1 and abs(o.pnls[0] - 0.03) < 1e-9


def test_endgame_loss_is_the_whole_stake():
    """The asymmetry that makes endgame dangerous: win pennies, lose dollars."""
    o = endgame([([(0, 0.97)], 0, False)], 0.95, NOFEE, slip=0.0)
    assert abs(o.pnls[0] + 0.97) < 1e-9


def test_overreaction_charges_the_round_trip_both_ways():
    tape = [(0, 0.50), (100, 0.65), (400, 0.65)]      # jump, then flat
    fade, mom = overreaction([tape], jump=0.10, lookback_s=300, hold_s=300,
                             fee=NOFEE, half_spread=0.005)
    assert abs(fade.pnls[0] + 0.01) < 1e-9 and abs(mom.pnls[0] + 0.01) < 1e-9


def test_overreaction_one_entry_per_tape():
    tape = [(i * 10, 0.50 + (0.2 if i % 2 else 0)) for i in range(50)]
    fade, _ = overreaction([tape], 0.10, 300, 60, NOFEE)
    assert fade.n == 1


def test_cross_venue_buys_toward_the_other_price():
    o = toward_other_venue([(0.50, 0.60, True), (0.50, 0.40, False)], 0.05, NOFEE, slip=0.0)
    assert o.n == 2 and all(p > 0 for p in o.pnls)
    assert toward_other_venue([(0.50, 0.52, True)], 0.05, NOFEE).n == 0


def test_outcome_t_is_zero_without_variance_rather_than_infinite():
    """Zero losses in a small endgame sample gave t = 70 in exploration.
    Report it as uninformative, not as certainty."""
    assert Outcome([0.03] * 10).t == 0.0
    assert Outcome([0.03] * 10).losses == 0
