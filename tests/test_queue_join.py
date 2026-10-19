"""scripts/queue_join.py: the committed queue-counted touch check."""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "queue_join", Path(__file__).resolve().parents[1] / "scripts" / "queue_join.py")
qj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qj)


def test_queue_must_be_eaten_before_a_join_fills_and_front_fills_at_once():
    start = 10_000.0
    samples = [(0.0, 0.40, 0.42, 500.0, 300.0), (600.0, 0.40, 0.42, 500.0, 300.0)]
    prints = [(10.0, 0.40, 200.0), (20.0, 0.40, 200.0), (30.0, 0.39, 200.0)]   # 600 at/through 0.40
    js = qj.joins_for_game(samples, prints, start, "KXNHLGAME-X-A")
    bid = [j for j in js if j["kind"] == "JOIN-QUEUE"]
    front = [j for j in js if j["kind"] == "FRONT"]
    assert bid[0]["filled"] is True                    # 600 > 500 queued ahead
    assert front[0]["filled"] is True
    # close mid is 0.41; bid at 0.40 earns +1c gross, less the maker fee
    assert bid[0]["clv"] == pytest.approx(0.01 - qj.kalshi_maker_fee_per_share("KXNHLGAME-X-A", price=0.40))
    # the second join (600 s later) sees no later prints: unfilled
    assert any(not j["filled"] for j in bid)


def test_a_thin_tape_never_fills_a_deep_queue_and_new_price_needs_a_move():
    samples = [(0.0, 0.40, 0.42, 5000.0, 300.0), (400.0, 0.41, 0.42, 10.0, 300.0)]
    prints = [(50.0, 0.40, 100.0), (450.0, 0.41, 5.0)]
    js = qj.joins_for_game(samples, prints, 10_000.0, "")
    q = [j for j in js if j["kind"] == "JOIN-QUEUE" and j["clv"] is None]
    assert q                                            # 100 printed < 5000 queued: no fill
    np_ = [j for j in js if j["kind"] == "NEW-PRICE"]
    assert len(np_) == 1 and np_[0]["filled"]          # only the bid moved (0.40 -> 0.41)


def test_nothing_after_the_start_counts():
    samples = [(0.0, 0.40, 0.42, 0.0, 0.0)]
    prints = [(200.0, 0.40, 10.0)]
    js = qj.joins_for_game(samples, prints, 100.0, "")
    assert not any(j["filled"] for j in js)
