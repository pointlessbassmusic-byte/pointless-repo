from datetime import datetime, timedelta, timezone

import pytest

from sportsbot.bot.arb import find_bundle_arb, find_cross_venue_arb
from sportsbot.bot.matching import match_entity, normalize, similarity
from sportsbot.bot.risk import RiskConfig, RiskManager
from sportsbot.bot.strategy import StrategyConfig, evaluate_market, walk_book
from sportsbot.core.staking import StakingConfig
from sportsbot.core.types import (
    BookLevel,
    Exchange,
    MarketInfo,
    MarketQuote,
    Prediction,
    Side,
    Sport,
)
from sportsbot.data.store import Store
from sportsbot.exchanges.kalshi import kalshi_taker_fee
from sportsbot.exchanges.paper import PaperExchange
from sportsbot.exchanges.polymarket import taker_fee

NO_FEE = lambda price, shares: 0.0  # noqa: E731


def _market(**kw):
    defaults = dict(
        exchange=Exchange.POLYMARKET, market_id="m1", yes_token_id="t1",
        no_token_id="t2", question="A vs B", slug="a-b", sport=Sport.TENNIS,
        home="Alpha Player", away="Beta Player",
        start_time=datetime.now(timezone.utc) + timedelta(hours=6),
    )
    defaults.update(kw)
    return MarketInfo(**defaults)


def _quote(bid=0.48, ask=0.50, depth=200.0, market_id="m1"):
    return MarketQuote(
        market_id=market_id, bid=bid, ask=ask,
        bids=[BookLevel(price=bid, size=depth)],
        asks=[BookLevel(price=ask, size=depth)],
    )


def _prediction(prob=0.60, uncertainty=0.05):
    return Prediction(market_id="m1", sport=Sport.TENNIS, model="test",
                      prob_yes=prob, uncertainty=uncertainty)


NO_EXPOSURE = {"total": 0.0, "by_sport": {}, "by_market": {}, "open_positions": 0}


class TestMatching:
    def test_normalize(self):
        assert normalize("Alcaraz, Carlos Jr.") == "alcaraz carlos"
        assert normalize("São Paulo") == "sao paulo"

    def test_similarity_truncated(self):
        assert similarity("Vadim Veacesl", "vadim veaceslav") > 0.8

    def test_match_entity_threshold_and_ambiguity(self):
        cands = ["carlos alcaraz", "jannik sinner"]
        assert match_entity("Carlos Alcaraz", cands) == "carlos alcaraz"
        assert match_entity("Zzz Qqq", cands) is None
        # exact normalized match is trusted even with a near-identical runner-up
        assert match_entity("john smith", ["john smith", "jon smith"]) == "john smith"
        # fuzzy + two near-identical candidates -> ambiguous -> skip
        assert match_entity("j smith", ["john smith", "jon smith"]) is None


class TestStrategy:
    def test_walk_book(self):
        levels = [(0.50, 100.0), (0.52, 100.0)]
        avg, filled = walk_book(levels, max_price=0.52, max_size=150.0)
        assert filled == 150.0
        assert avg == pytest.approx((100 * 0.50 + 50 * 0.52) / 150)

    def test_positive_edge_yes(self):
        intent = evaluate_market(_market(), _quote(), _prediction(0.60),
                                 StakingConfig(bankroll=1000),
                                 StrategyConfig(model_weight=1.0),
                                 NO_FEE, NO_EXPOSURE)
        assert intent is not None and intent.side == Side.YES
        assert intent.edge > 0

    def test_no_side_taken_when_model_low(self):
        intent = evaluate_market(_market(), _quote(), _prediction(0.35),
                                 StakingConfig(bankroll=1000),
                                 StrategyConfig(model_weight=1.0),
                                 NO_FEE, NO_EXPOSURE)
        assert intent is not None and intent.side == Side.NO

    def test_no_bet_when_market_agrees(self):
        intent = evaluate_market(_market(), _quote(), _prediction(0.50),
                                 StakingConfig(bankroll=1000),
                                 StrategyConfig(model_weight=1.0),
                                 NO_FEE, NO_EXPOSURE)
        assert intent is None

    def test_blend_kills_marginal_edge(self):
        # 55% model vs 49% mid: with 0.3 weight the blend is ~50.8% -> no bet
        intent = evaluate_market(_market(), _quote(), _prediction(0.55),
                                 StakingConfig(bankroll=1000),
                                 StrategyConfig(model_weight=0.3),
                                 NO_FEE, NO_EXPOSURE)
        assert intent is None

    def test_wide_spread_skipped(self):
        intent = evaluate_market(_market(), _quote(bid=0.40, ask=0.55),
                                 _prediction(0.70), StakingConfig(bankroll=1000),
                                 StrategyConfig(model_weight=1.0),
                                 NO_FEE, NO_EXPOSURE)
        assert intent is None

    def test_uncertain_prediction_skipped(self):
        intent = evaluate_market(_market(), _quote(), _prediction(0.65, uncertainty=0.3),
                                 StakingConfig(bankroll=1000),
                                 StrategyConfig(model_weight=1.0),
                                 NO_FEE, NO_EXPOSURE)
        assert intent is None

    def test_fees_reduce_edge(self):
        cfg = StrategyConfig(model_weight=1.0, post_inside_spread=False)
        no_fee = evaluate_market(_market(), _quote(), _prediction(0.60),
                                 StakingConfig(bankroll=1000), cfg, NO_FEE, NO_EXPOSURE)
        with_fee = evaluate_market(_market(), _quote(), _prediction(0.60),
                                   StakingConfig(bankroll=1000), cfg,
                                   taker_fee, NO_EXPOSURE)
        assert no_fee.edge > with_fee.edge

    def test_per_sport_override(self):
        cfg = StrategyConfig(model_weight=1.0,
                             min_edge_override={"tennis": 0.5})
        intent = evaluate_market(_market(), _quote(), _prediction(0.60),
                                 StakingConfig(bankroll=1000), cfg, NO_FEE, NO_EXPOSURE)
        assert intent is None  # 10% edge < 50% override


class TestRisk:
    def _mgr(self, tmp_path, **kw):
        store = Store(str(tmp_path / "t.sqlite"))
        cfg = RiskConfig(**kw) if kw else RiskConfig()
        return RiskManager(cfg, store, mode="paper"), store

    def test_ok_by_default(self, tmp_path):
        mgr, _ = self._mgr(tmp_path)
        ok, reason = mgr.check_global()
        assert ok, reason

    def test_daily_loss_limit(self, tmp_path):
        mgr, store = self._mgr(tmp_path, daily_loss_limit=50.0)
        bid = store.record_bet("m", "tennis", "yes", 0.6, 0.5, 100.0, 200.0,
                               0.1, "paper", "paper")
        store.settle_bet(bid, outcome=0, pnl=-100.0)
        ok, reason = mgr.check_global()
        assert not ok and "loss" in reason

    def test_drawdown_kill_switch_persists(self, tmp_path):
        mgr, store = self._mgr(tmp_path, max_drawdown=50.0)
        bid = store.record_bet("m", "tennis", "yes", 0.6, 0.5, 100.0, 200.0,
                               0.1, "paper", "paper")
        store.settle_bet(bid, outcome=0, pnl=-100.0)
        ok, _ = mgr.check_global()
        assert not ok
        assert mgr.kill_switch_tripped()
        mgr.reset_kill_switch()
        # still blocked by drawdown until history clears, but switch resets
        assert not mgr.kill_switch_tripped()

    def test_stale_quote_veto(self, tmp_path):
        mgr, _ = self._mgr(tmp_path)
        from sportsbot.core.types import BetIntent

        q = _quote()
        q.ts = datetime.now(timezone.utc) - timedelta(seconds=999)
        intent = BetIntent(market=_market(), side=Side.YES, prob=0.6, price=0.5,
                           size=10, edge=0.1, kelly_fraction=0.01)
        ok, reason = mgr.check_intent(intent, q)
        assert not ok and "stale" in reason

    def test_near_start_veto(self, tmp_path):
        mgr, _ = self._mgr(tmp_path)
        from sportsbot.core.types import BetIntent

        m = _market(start_time=datetime.now(timezone.utc) + timedelta(minutes=2))
        intent = BetIntent(market=m, side=Side.YES, prob=0.6, price=0.5,
                           size=10, edge=0.1, kelly_fraction=0.01)
        ok, reason = mgr.check_intent(intent, _quote())
        assert not ok and "start" in reason


class TestPaperExchange:
    def test_fill_walks_real_book(self):
        paper = PaperExchange(starting_balance=1000.0)
        from sportsbot.core.types import Order, OrderType

        order = Order(market_id="m1", token_id="t1", side=Side.YES,
                      order_type=OrderType.LIMIT, price=0.50, size=100.0)
        q = _quote(bid=0.48, ask=0.50, depth=60.0)
        placed = paper.place_order(order, quote=q)
        assert placed.filled == 60.0  # only book depth fills
        assert paper.get_balance() == pytest.approx(1000.0 - 60 * 0.50)

    def test_settlement(self):
        paper = PaperExchange(starting_balance=1000.0)
        from sportsbot.core.types import Order, OrderType

        order = Order(market_id="m1", token_id="t1", side=Side.YES,
                      order_type=OrderType.LIMIT, price=0.50, size=100.0)
        paper.place_order(order, quote=_quote(depth=100.0))
        pnl = paper.settle("m1", yes_won=True)
        assert pnl == pytest.approx(100 * (1 - 0.50))
        assert paper.get_balance() == pytest.approx(1000.0 + pnl - 0.0)

    def test_insufficient_balance_rejected(self):
        paper = PaperExchange(starting_balance=10.0)
        from sportsbot.core.types import Order, OrderStatus, OrderType

        order = Order(market_id="m1", token_id="t1", side=Side.YES,
                      order_type=OrderType.LIMIT, price=0.50, size=100.0)
        placed = paper.place_order(order, quote=_quote(depth=100.0))
        assert placed.status == OrderStatus.REJECTED


class TestArb:
    def test_bundle_arb_detection(self):
        # YES ask 0.46, NO ask = 1-0.52 = 0.48 -> cost 0.94, profit 0.06
        q = _quote(bid=0.52, ask=0.46)
        opp = find_bundle_arb(_market(), q, NO_FEE)
        assert opp is not None
        assert opp.profit_per_pair == pytest.approx(0.06, abs=1e-6)

    def test_no_bundle_arb_normal_book(self):
        assert find_bundle_arb(_market(), _quote(bid=0.48, ask=0.50), NO_FEE) is None

    def test_fees_kill_thin_bundle(self):
        q = _quote(bid=0.52, ask=0.47)  # 1% gross
        assert find_bundle_arb(_market(), q, taker_fee) is None

    def test_cross_venue_arb(self):
        pm = _market()
        km = _market(exchange=Exchange.KALSHI, market_id="KX1")
        pm_q = _quote(bid=0.44, ask=0.46)   # pm YES cheap
        k_q = _quote(bid=0.56, ask=0.58, market_id="KX1")  # kalshi prices A at 57
        # buy pm YES @0.46 + kalshi NO @ (1-0.56)=0.44 -> cost 0.90
        opp = find_cross_venue_arb(pm, pm_q, km, k_q, aligned=True, pm_fee=NO_FEE,
                                   k_fee=NO_FEE)
        assert opp is not None
        assert opp.profit_per_pair == pytest.approx(0.10, abs=1e-6)


class TestKalshiFee:
    def test_formula(self):
        assert kalshi_taker_fee(0.5, 100, 1.0) == 1.75
        assert kalshi_taker_fee(0.5, 100, 0.5) == 0.88  # MLB half fees
        assert kalshi_taker_fee(0.05, 100, 1.0) == pytest.approx(0.34, abs=0.01)
