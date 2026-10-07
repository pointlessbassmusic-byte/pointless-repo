"""Kalshi LIP recorder: scoring and fill simulation (pure functions)."""
import json

from sportsbot.signals.lip import (Recorder, order_score, reference_price, side_share,
                                   simulate_fill)


def test_reference_price_walks_down_to_fifth_of_target():
    bids = [(0.40, 50), (0.41, 30), (0.39, 500)]
    # target 500 -> need 100: 0.41 (30) + 0.40 (50) = 80 < 100, 0.39 reaches it
    assert reference_price(bids, 500) == 0.39
    assert reference_price(bids, 200) == 0.40      # need 40: 30 + 50 >= 40 at 0.40
    assert reference_price([(0.10, 1)], 1000) == 0.10   # thin side -> worst bid
    assert reference_price([], 100) is None


def test_order_score_discounts_per_tick_below_reference():
    assert order_score(0.40, 100, 0.40, 0.5) == 100
    assert order_score(0.42, 100, 0.40, 0.5) == 100    # better than ref: full credit
    assert order_score(0.38, 100, 0.40, 0.5) == 25     # two ticks below: 0.5^2


def test_side_share_includes_our_order_in_the_book():
    bids = [(0.40, 100)]
    # we join at 0.40 with 100; target 500 -> need 100, ref = 0.40 -> 50/50
    assert abs(side_share(bids, 0.40, 100, 500, 0.5) - 0.5) < 1e-9
    # one tick behind: ref 0.40 (the 100 there reaches 100), ours scores 50 of 150
    assert abs(side_share(bids, 0.39, 100, 500, 0.5) - 50 / 150) < 1e-9


def test_simulate_fill_respects_queue_and_side():
    def t(yp, n, side):
        return {"yes_price": yp, "count": n, "taker_side": side}

    # YES bid at 0.40 with 50 ahead: 30 traded at 0.40 -> not filled; 30 more -> filled
    assert not simulate_fill(0.40, 50, [t(0.40, 30, "no")], "yes")
    assert simulate_fill(0.40, 50, [t(0.40, 30, "no"), t(0.40, 30, "no")], "yes")
    assert simulate_fill(0.40, 1e9, [t(0.39, 1, "no")], "yes")        # through: always
    assert not simulate_fill(0.40, 0, [t(0.39, 5, "yes")], "yes")     # taker buying: not us
    # NO bid at 0.55 (= YES ask 0.45): taker buys YES at 0.46 -> NO price 0.54 < 0.55
    assert simulate_fill(0.55, 1e9, [t(0.46, 1, "yes")], "no")


class _Resp:
    def __init__(self, d):
        self.status_code, self._d = 200, d

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class _Client:
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, params=None):
        for k, v in self.routes.items():
            if url.endswith(k):
                return _Resp(v)
        raise AssertionError(url)


def test_recorder_stores_programs_snapshots_and_never_posts(tmp_path):
    routes = {
        "/incentive_programs": {"incentive_programs": [{
            "id": "p1", "market_ticker": "KXHIGHNY-26OCT02-T70", "incentive_type": "liquidity",
            "start_date": "2026-01-01T00:00:00Z", "end_date": "2099-01-01T00:00:00Z",
            "period_reward": 200000, "discount_factor_bps": 5000, "target_size_fp": "300.00"}],
            "next_cursor": ""},
        "/markets/KXHIGHNY-26OCT02-T70/orderbook": {"orderbook_fp": {
            "yes_dollars": [["0.40", "10.00"]], "no_dollars": [["0.55", "20.00"]]}},
    }
    c = _Client(routes)
    r = Recorder(str(tmp_path / "lip.sqlite"), panel_size=5, rps=1000, client=c,
                 categories={"KXHIGHNY": "Climate and Weather"})
    assert r.refresh_programs() == 1
    r.refill_panel()
    r.snapshot("KXHIGHNY-26OCT02-T70")
    row = r.db.execute("SELECT yes_bids, no_bids FROM snapshots").fetchone()
    assert json.loads(row[0]) == [[0.40, 10.0]] and json.loads(row[1]) == [[0.55, 20.0]]
    assert r.db.execute("SELECT category, weight FROM panel").fetchone() == ("Climate and Weather", 5.0)
    assert not hasattr(c, "post")      # the recorder has no write path at all


def test_pull_trades_logs_pull_time_even_with_no_trades(tmp_path):
    # the analysis only credits rewards up to the last pull, so a market with no
    # trades must still record that it was checked
    c = _Client({"/markets/trades": {"trades": [], "cursor": ""}})
    r = Recorder(str(tmp_path / "lip.sqlite"), rps=1000, client=c)
    r.db.execute("INSERT INTO panel VALUES ('T', 'x', 100, 1.0)")
    r.pull_trades("T")
    assert r.db.execute("SELECT ts FROM pulls WHERE ticker='T'").fetchone()[0] > 100
