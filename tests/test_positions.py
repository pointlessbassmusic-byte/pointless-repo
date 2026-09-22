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
    category_report,
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


def test_category_report_reflects_adaptive_state():
    def rows(sport, clv, n, pnl, outcome):
        return [{"sport": sport, "entry_price": 0.5, "closing_price": 0.5 + clv,
                 "pnl": pnl, "outcome": outcome} for _ in range(n)]

    # store order is newest first; tennis (losing) is the most recent run
    settled = rows("tennis", -0.02, 40, -2.0, 0) + rows("baseball", 0.02, 40, 3.0, 1)
    rep = category_report(settled, {}, 0.03, 50.0, {}, {}, 0.25, 250.0)

    t, b = rep["by_sport"]["tennis"], rep["by_sport"]["baseball"]
    assert t["tightened"] and t["min_edge"] == 0.05 and t["max_stake"] == 25.0
    assert not b["tightened"] and b["min_edge"] == 0.03
    assert b["pnl"] == 120.0 and t["pnl"] == -80.0
    assert t["hit_rate"] == 0.0 and b["hit_rate"] == 1.0
    # oldest-first: baseball +120 peak, then tennis -80 -> drawdown 80
    assert rep["current_drawdown"] == 80.0
    assert abs(rep["effective_kelly"] - 0.25 * (1 - 80.0 / 250.0)) < 1e-9
    assert rep["tightened"] == ["tennis"]


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
        exchange=ex, store=store, fee_fn=lambda p, s: 0.0,
        decision_fee_fn=lambda market_id: (lambda p, s: 0.0),
        mode="paper", account="sim", _record_exit=lambda *a, **k: None)
    # market collapsed to a 0.20 bid: hard stop (value 19.5 < 50% of 50)
    exits = Runner._manage_positions(
        stub, {"m1": (None, quote(0.20, 0.24))}, StrategyConfig())
    assert exits == 1
    assert store.open_bets() == [] and ex.get_positions() == []
    row = store.settled_bets()[0]
    assert row["pnl"] == -30.0 and row["outcome"] is None
    assert abs(ex.balance - 970.0) < 1e-6


def test_kalshi_close_position_buys_opposite_ioc(monkeypatch):
    """Closing YES on Kalshi = IOC buy of NO (venue nets the pair), limit
    priced so proceeds never drop below min_price."""
    from sportsbot.core.types import OrderStatus, OrderType, Position
    from sportsbot.exchanges.kalshi import KalshiClient, kalshi_taker_fee

    c = KalshiClient(env="demo")
    pos = Position(market_id="KXATPMATCH-X", side=Side.YES, size=100.0,
                   avg_price=0.50)
    monkeypatch.setattr(c, "get_positions", lambda: [pos])
    captured = {}

    def fake_place(order):
        captured["order"] = order
        order.filled = order.size
        order.status = OrderStatus.FILLED
        return order

    monkeypatch.setattr(c, "place_order", fake_place)
    r = c.close_position("KXATPMATCH-X", Side.YES, quote(0.30, 0.34))
    o = captured["order"]
    assert o.side == Side.NO and o.order_type == OrderType.IOC
    assert abs(o.price - 0.98) < 1e-9        # 1 - min_price floor
    assert o.size == 100.0
    assert r["closed_size"] == 100.0 and abs(r["avg_price"] - 0.30) < 1e-9
    fee = kalshi_taker_fee(0.30, 100.0)
    assert abs(r["proceeds"] - (30.0 - fee)) < 1e-6
    # no position on the other side -> nothing to close
    assert c.close_position("KXATPMATCH-X", Side.NO, quote(0.30, 0.34)) is None


def test_runner_partial_close_banks_proceeds(tmp_path):
    """A partial IOC fill banks its proceeds; bets close only once the whole
    aggregate is out, with combined proceeds."""
    from types import SimpleNamespace

    from sportsbot.bot.runner import Runner
    from sportsbot.bot.strategy import StrategyConfig

    store = Store(str(tmp_path / "t.sqlite"))
    store.record_bet("m1", "tennis", "yes", 0.6, 0.5, 50.0, 100.0,
                     0.05, "paper", "paper")
    fills = [{"closed_size": 40.0, "avg_price": 0.30, "proceeds": 12.0,
              "fee": 0.0},
             {"closed_size": 60.0, "avg_price": 0.30, "proceeds": 18.0,
              "fee": 0.0}]
    ex = SimpleNamespace(close_position=lambda *a, **k: fills.pop(0))
    stub = SimpleNamespace(positions=PositionConfig(min_hold_minutes=0.0),
                           exchange=ex, store=store, fee_fn=lambda p, s: 0.0,
                           decision_fee_fn=lambda market_id: (lambda p, s: 0.0),
                           mode="paper", account="sim",
                           _record_exit=lambda *a, **k: None)
    quoted = {"m1": (None, quote(0.20, 0.24))}  # hard-stop territory

    assert Runner._manage_positions(stub, quoted, StrategyConfig()) == 0
    assert len(store.open_bets()) == 1          # still open after partial
    bank = store.get_kv("partial_close:m1:yes")
    assert bank == {"closed": 40.0, "proceeds": 12.0}

    assert Runner._manage_positions(stub, quoted, StrategyConfig()) == 1
    assert store.open_bets() == []
    assert store.settled_bets()[0]["pnl"] == -20.0   # 12+18 proceeds - 50
    assert store.get_kv("partial_close:m1:yes") is None


def test_settlement_reconciles_partial_close_bank(tmp_path):
    """A bet that was partially closed early settles on the REMAINING size
    plus the banked proceeds — never the full original payout."""
    from types import SimpleNamespace

    from sportsbot.bot.runner import Runner

    store = Store(str(tmp_path / "t.sqlite"))
    store.record_bet("m1", "tennis", "yes", 0.6, 0.5, 50.0, 100.0,
                     0.05, "paper", "paper")
    store.set_kv("partial_close:m1:yes", {"closed": 40.0, "proceeds": 12.0})
    stub = SimpleNamespace(
        store=store,
        data_client=SimpleNamespace(get_resolution=lambda m: True),
        executor=SimpleNamespace(settle_paper=lambda m, y: 0.0))
    assert Runner._settle_resolved(stub) == 1
    row = store.settled_bets()[0]
    # won: 60 remaining shares pay $60, plus $12 banked, minus $50 stake
    assert row["outcome"] == 1 and row["pnl"] == 22.0
    assert store.get_kv("partial_close:m1:yes") is None


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


def test_kalshi_event_sibling_pairing():
    """Kalshi parses home==away (no_sub_title mirrors the player); pairing
    within an event recovers the true opponent from the sibling market."""
    from sportsbot.core.types import Exchange, MarketInfo, Sport
    from sportsbot.exchanges.kalshi import KalshiClient

    def mi(ticker, player, event):
        return MarketInfo(exchange=Exchange.KALSHI, market_id=ticker,
                          question=f"Will {player} win?", slug=ticker,
                          sport=Sport.TENNIS, home=player, away=player,
                          meta={"event_ticker": event})

    a = mi("KXATPMATCH-X-SVR", "Dalibor Svrcina", "KXATPMATCH-X")
    b = mi("KXATPMATCH-X-SEK", "Philip Sekulic", "KXATPMATCH-X")
    lone = mi("KXATPMATCH-Y-FOO", "Solo Player", "KXATPMATCH-Y")
    KalshiClient._pair_event_siblings([a, b, lone])
    assert a.home == "Dalibor Svrcina" and a.away == "Philip Sekulic"
    assert b.home == "Philip Sekulic" and b.away == "Dalibor Svrcina"
    assert lone.away == "Solo Player"  # unpaired: left as parsed (skipped)


def test_kalshi_market_info_time_fields():
    """occurrence_datetime is the match start; close_time/expiration_time
    are far-future legal bounds and must never become the start proxy
    (the pre-match cutoff would never trigger -> in-play entries)."""
    from sportsbot.core.types import Sport
    from sportsbot.exchanges.kalshi import KalshiClient

    raw = {"ticker": "KXATPMATCH-26SEP22SVRSEK-SVR",
           "title": "Dalibor Svrcina wins",
           "yes_sub_title": "Dalibor Svrcina",
           "no_sub_title": "Dalibor Svrcina",
           "event_ticker": "KXATPMATCH-26SEP22SVRSEK",
           "occurrence_datetime": "2026-09-22T07:00:00Z",
           "expected_expiration_time": "2026-09-22T07:00:00Z",
           "close_time": "2026-10-06T04:00:00Z",
           "expiration_time": "2026-10-06T04:00:00Z",
           "status": "active"}
    client = KalshiClient(env="demo")
    mi = client._to_market_info(raw, Sport.TENNIS, "KXATPMATCH")
    assert mi.start_time is not None and mi.start_time.day == 22
    assert mi.close_time is not None and mi.close_time.day == 22
    assert mi.close_time.month == 9  # never the Oct 6 legal bound


def test_kalshi_fee_marginal_vs_total():
    """The ceil in kalshi_taker_fee is PER ORDER, so it is not linear in
    contracts: f(p, 1.0) is not the marginal per-share fee. Decision paths
    must use kalshi_fee_per_share instead (audit finding, 2026-09-22)."""
    from sportsbot.exchanges.kalshi import (
        kalshi_fee_multiplier,
        kalshi_fee_per_share,
        kalshi_taker_fee,
    )

    # the trap: whole-cent quantisation inflates the modelled per-share fee
    assert kalshi_taker_fee(0.20, 1.0) == 0.02
    assert abs(kalshi_fee_per_share(0.20) - 0.0112) < 1e-9
    # marginal is linear and never rounds up
    for p in (0.15, 0.2, 0.5, 0.85):
        assert kalshi_fee_per_share(p) <= kalshi_taker_fee(p, 1.0)
        assert abs(kalshi_fee_per_share(p) * 10 - 0.07 * 10 * p * (1 - p)) < 1e-12
    # total cost keeps the venue's ceil-once-per-order behaviour
    assert kalshi_taker_fee(0.5, 100.0) == 1.75

    # series multiplier: prefix match only, never a loose substring
    assert kalshi_fee_multiplier("KXMLBGAME-26SEP24-SD") == 0.5
    assert kalshi_fee_multiplier("KXATPMATCH-x") == 1.0
    assert kalshi_fee_multiplier("KXWTAMLBFAKE-x") == 1.0
    assert kalshi_fee_multiplier("") == 1.0
    assert abs(kalshi_fee_per_share(0.46, 0.5) - 0.07 * 0.5 * 0.46 * 0.54) < 1e-12


def test_runner_decision_fee_fn_uses_marginal_and_series_multiplier():
    """Runner hands decision paths the marginal fee with the market's own
    multiplier; non-Kalshi venues keep their (already linear) fee_fn."""
    from types import SimpleNamespace

    from sportsbot.bot.runner import Runner

    kalshi_stub = SimpleNamespace(venue="kalshi", fee_fn=lambda p, s: 99.0)
    f_mlb = Runner.decision_fee_fn(kalshi_stub, "KXMLBGAME-26SEP24-SD")
    f_tennis = Runner.decision_fee_fn(kalshi_stub, "KXATPMATCH-x")
    # MLB pays half of tennis at the same price, and neither is the
    # whole-cent-quantised $0.02
    assert abs(f_mlb(0.46, 1.0) - 0.5 * f_tennis(0.46, 1.0)) < 1e-12
    assert f_tennis(0.20, 1.0) < 0.02
    # linear in shares
    assert abs(f_tennis(0.20, 10.0) - 10 * f_tennis(0.20, 1.0)) < 1e-12

    other = SimpleNamespace(venue="polymarket", fee_fn=lambda p, s: 42.0)
    assert Runner.decision_fee_fn(other, "anything")(0.5, 1.0) == 42.0
