from src.models.devig import consensus_probs, devig_proportional
from src.strategy.edge import kelly_stake


def test_devig_proportional_sums_to_one():
    probs = devig_proportional({"A": 1.91, "B": 1.91})
    assert abs(sum(probs.values()) - 1.0) < 1e-9
    assert abs(probs["A"] - 0.5) < 1e-9


def test_consensus_requires_min_books():
    books = [{"A": 1.91, "B": 1.91}, {"A": 1.87, "B": 1.95}]
    assert consensus_probs(books, min_books=3) == {}
    probs = consensus_probs(books, min_books=2)
    assert abs(sum(probs.values()) - 1.0) < 1e-9


def test_kelly_zero_when_no_edge():
    assert kelly_stake(fair=0.5, price=0.55, bankroll=1000, fraction=0.25) == 0.0


def test_kelly_positive_with_edge():
    stake = kelly_stake(fair=0.6, price=0.5, bankroll=1000, fraction=0.25)
    # f* = (0.6-0.5)/0.5 = 0.2; quarter-Kelly = 0.05 → $50
    assert abs(stake - 50.0) < 1e-6
