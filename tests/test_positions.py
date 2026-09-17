"""Position management: edge-driven exits, hard stops, anti-martingale
scaling, adaptive tightening — and the accounting for early closes.

The invariant under test throughout: reacting to losses only ever REDUCES
risk (close, scale down, tighten). Nothing here doubles down or flips a
position because it is losing.
"""

from datetime import datetime, timedelta, timezone

from sportsbot.bot.positions import (
    PositionConfig,
    adaptive_overrides,
    aggregate_open_bets,
    evaluate_exit,
    scaled_kelly,
)
from sportsbot.core.books import sell_levels, walk_sell
from sportsbot.core.calibration import BetRecord, PerformanceTracker
from sportsbot.core.types import BookLevel, MarketQuote, Side
from sportsbot.data.store import Store
from sportsbot.exchanges.paper import PaperExchange


def quote(bid, ask, bid_size=500.0, ask_size=500.0):
    return MarketQuote(
        market_id="m1", bid=bid, ask=ask,
        bids=[BookLevel(price=bid, size=bid_size)],
        asks=[BookLevel(price=ask, size=ask_size)],
    )


def agg(side="yes", model_prob=0.60, size=100.0, stake=50.0, age_minutes=120):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat()
    return {"market_id": "m1", "side": side, "size": size, "stake": stake,
            "model_prob": model_prob, "last_ts": ts, "bets": [(1, stake)]}


CFG = PositionConfig(exit_edge=-0.05, stop_fraction=0.5, min_hold_minutes=30)


# ---------------------------------------------------------------- book math

def test_sell_levels_frames():
    q = quote(0.40, 0.44)
    assert sell_levels(q, Side.YES)[0][0] == 0.40          # YES sells to bid
    assert sell_levels(q, Side.NO)[0][0] == 0.56           # NO sells at 1-ask


def test_walk_sell_walks_down_and_respects_floor():
    levels = [(0.40, 10.0), (0.35, 10.0), (0.01, 100.0)]
    avg, sold = walk_sell(levels, min_price=0.02, max_size=30.0)
    assert sold == 20.0                                    # 0.01 below floor
    assert abs(avg - 0.375) < 1e-9


# ---------------------------------------------------------------- exit rule

def test_holds_when_edge_intact():
    # Entered YES at 0.50 with model 0.60; market unchanged: hold.
    d = evaluate_exit(agg(), quote(0.49, 0.51), CFG)
    assert not d.close


def test_edge_reversal_closes_a_losing_position():
    # Market collapsed to 0.20: blend 0.3*0.6 + 0.7*0.205 ≈ 0.32 vs exit
    # ~0.19 net -> hold edge +0.13? No: exit 0.20 bid; hold EV 0.32 > exit.
    # Collapse further with a weak model prob so the blend goes under.
    a = agg(model_prob=0.35)
    d = evaluate_exit(a, quote(0.30, 0.34), CFG)
    # blend = 0.3*0.35 + 0.7*0.32 = 0.329; exit net 0.295 -> hold edge +0.034
    assert not d.close  # still worth more held than sold: no panic sell

    # But when holding is worth clearly LESS than the book pays (an
    # overpriced exit), close: model says 0.10 against a 0.42 bid.
    a = agg(model_prob=0.10)
    d = evaluate_exit(a, quote(0.30, 0.40), CFG)  # blend≈0.275, exit≈0.295
    assert not d.close  # -0.02 > exit_edge -0.05: inside the hysteresis band
    d = evaluate_exit(a, quote(0.42, 0.52), CFG)  # blend≈0.359, exit≈0.415
    assert d.close and "edge reversed" in d.reason


def test_hard_stop_cuts_the_tail():
    # Cost basis 50 for 100 shares @0.50; market now bids 0.20 -> value 19.5
    # < 50% of stake even though the blend tracks the market (no model veto).
    d = evaluate_exit(agg(model_prob=0.60), quote(0.20, 0.24), CFG)
    assert d.close and "hard stop" in d.reason


def test_min_hold_prevents_churn():
    d = evaluate_exit(agg(model_prob=0.10, age_minutes=5),
                      quote(0.38, 0.48), CFG)
    assert not d.close and d.reason == "min hold"


def test_no_liquidity_means_hold_not_dump():
    a = agg(model_prob=0.10)
    q = MarketQuote(market_id="m1", bid=0.01, ask=0.99,
                    bids=[BookLevel(price=0.01, size=500.0)], asks=[])
    d = evaluate_exit(a, q, CFG)
    assert not d.close  # below min_exit_price: never dump into a dead book


# ------------------------------------------------------- anti-martingale

def test_scaled_kelly_only_scales_down():
    base = 0.25
    assert scaled_kelly(base, 0.0, 250.0) == base
    assert scaled_kelly(base, 125.0, 250.0) == base * 0.5
    assert scaled_kelly(base, 250.0, 250.0) == base * 0.25   # floor
    assert scaled_kelly(base, 500.0, 250.0) == base * 0.25   # never below
    for dd in (0.0, 50.0, 150.0, 400.0):
        assert scaled_kelly(base, dd, 250.0) <= base         # never UP


def test_adaptive_tightens_negative_clv_and_never_loosens():
    def rows(sport, clv, n):
        return [{"sport": sport, "entry_price": 0.50,
                 "closing_price": 0.50 + clv} for _ in range(n)]

    settled = rows("tennis", -0.02, 40) + rows("baseball", +0.02, 40) \
        + rows("table_tennis", -0.05, 10)          # too few to act on
    edge, stake, tightened = adaptive_overrides(
        settled, {"table_tennis": 0.05}, 0.03, {"table_tennis": 20.0}, 50.0,
        min_bets=30, tighten_edge=0.02, stake_cut=0.5)
    assert tightened == ["tennis"]
    assert edge["tennis"] == 0.05 and stake["tennis"] == 25.0
    assert edge["table_tennis"] == 0.05             # base kept: n < min_bets
    assert "baseball" not in edge                   # positive CLV: untouched
    # invariant: no override is ever looser than the configured base
    assert all(v >= 0.03 for v in edge.values())


# ------------------------------------------------------- close accounting

def test_paper_close_position_realizes_pnl():
    ex = PaperExchange(starting_balance=1000.0,
                       fee_fn=lambda price, size: 0.0)
    q_entry = quote(0.48, 0.50)
    from sportsbot.core.types import Order
    order = ex.place_order(Order(client_id="c1", market_id="m1",
                                 side=Side.YES, price=0.50, size=100.0),
                           quote=q_entry)
    assert order.filled == 100.0 and abs(ex.balance - 950.0) < 1e-6

    result = ex.close_position("m1", Side.YES, quote(0.30, 0.34))
    assert result["closed_size"] == 100.0
    assert abs(result["proceeds"] - 30.0) < 1e-6
    assert abs(ex.balance - 980.0) < 1e-6            # cut the loss at -20
    assert ex.get_positions() == []                  # flat
    assert ex.close_position("m1", Side.YES, quote(0.30, 0.34)) is None


def test_store_close_bet_accounting(tmp_path):
    store = Store(str(tmp_path / "t.sqlite"))
    bid = store.record_bet("m1", "tennis", "yes", 0.6, 0.5, 50.0, 100.0,
                           0.05, "paper", "paper")
    assert store.exposure_by()["total"] == 50.0
    store.close_bet(bid, pnl=-20.0, closing_price=0.30)
    assert store.open_bets() == []                   # no longer open
    assert store.exposure_by()["total"] == 0.0
    rows = store.settled_bets()
    assert len(rows) == 1 and rows[0]["pnl"] == -20.0
    assert rows[0]["outcome"] is None                # no fake win/lose


def test_tracker_counts_closed_pnl_but_not_in_brier():
    t = PerformanceTracker()
    t.add(BetRecord("m1", "yes", 0.6, 0.5, 50.0, outcome=1, pnl=50.0))
    t.add(BetRecord("m2", "yes", 0.6, 0.5, 50.0, outcome=None, pnl=-20.0,
                    closing_price=0.30))
    s = t.summary()
    assert s["pnl"] == 30.0 and s["n_closed_early"] == 1
    assert s["n_settled"] == 1
    assert abs(s["brier"] - 0.16) < 1e-9              # closed bet excluded
    assert t.drawdown() == 20.0                       # closed loss counts


def test_runner_exit_pass_wiring(tmp_path):
    """aggregate -> evaluate -> paper close -> store close_bet, end to end."""
    from types import SimpleNamespace

    from sportsbot.bot.runner import Runner
    from sportsbot.bot.strategy import StrategyConfig
    from sportsbot.core.types import Order

    store = Store(str(tmp_path / "t.sqlite"))
    ex = PaperExchange(starting_balance=1000.0, fee_fn=lambda p, s: 0.0)
    ex.place_order(Order(client_id="c1", market_id="m1", side=Side.YES,
                         price=0.50, size=100.0), quote=quote(0.48, 0.50))
    store.record_bet("m1", "tennis", "yes", 0.6, 0.5, 50.0, 100.0,
                     0.05, "paper", "paper")

    stub = SimpleNamespace(
        positions=PositionConfig(min_hold_minutes=0.0),
        exchange=ex, store=store, fee_fn=lambda p, s: 0.0)
    # market collapsed to a 0.20 bid: hard stop (value 19.5 < 50% of 50)
    exits = Runner._manage_positions(
        stub, {"m1": (None, quote(0.20, 0.24))}, StrategyConfig())
    assert exits == 1
    assert store.open_bets() == [] and ex.get_positions() == []
    row = store.settled_bets()[0]
    assert row["pnl"] == -30.0 and row["outcome"] is None
    assert abs(ex.balance - 970.0) < 1e-6


def test_aggregate_open_bets():
    bets = [
        {"id": 1, "market_id": "m1", "side": "yes", "size": 60.0,
         "stake": 30.0, "model_prob": 0.60, "ts": "2026-09-17T00:00:00+00:00"},
        {"id": 2, "market_id": "m1", "side": "yes", "size": 40.0,
         "stake": 20.0, "model_prob": 0.55, "ts": "2026-09-17T01:00:00+00:00"},
    ]
    aggs = aggregate_open_bets(bets)
    a = aggs[("m1", "yes")]
    assert a["size"] == 100.0 and a["stake"] == 50.0
    assert abs(a["model_prob"] - 0.58) < 1e-9         # stake-weighted
    assert a["last_ts"].startswith("2026-09-17T01")   # newest for hold timer
