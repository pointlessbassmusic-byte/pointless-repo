"""Two carry-overs from the arbitrage desk: 'distance to the bar' as a
daily record and board panel, and locked-pair accounting for the maker
test (both sides filled on one market is a complete set)."""

from datetime import datetime, timedelta, timezone

import pytest

from sportsbot.bot.maker_report import format_maker_report, locked_pairs, maker_report
from sportsbot.bot.runner import Runner, _tally_edge
from sportsbot.bot.strategy import StrategyConfig
from sportsbot.core.staking import StakingConfig
from sportsbot.core.types import BetIntent, Exchange, MarketInfo, Side, Sport
from sportsbot.dashboard import _edge_bar, _svg_edge_bar, edge_bar_rows
from sportsbot.data.store import Store


def _market(sport=Sport.BASEBALL, mid="m1"):
    return MarketInfo(exchange=Exchange.POLYMARKET, market_id=mid, sport=sport, home="A", away="B",
                      start_time=datetime.now(timezone.utc) + timedelta(hours=3))


def test_tally_edge_reads_bets_near_misses_and_spread_blocks():
    seen = {}
    st = StakingConfig(min_edge=0.03)
    sc = StrategyConfig()
    sc.min_edge_override = {"tennis": 0.05}
    _tally_edge(seen, _market(), None, "yes edge +0.0074 vs the 0.030 bar: edge 0.007 < min 0.03", st, sc)
    _tally_edge(seen, _market(), None, "no edge +0.0250 vs the 0.030 bar: edge 0.025 < min 0.03", st, sc)
    _tally_edge(seen, _market(), None, "spread 0.550 wider than the 0.030 limit — fills would give back the edge", st, sc)
    intent = BetIntent(market=_market(), side=Side.YES, prob=0.6, price=0.5, size=2.0, edge=0.041,
                       kelly_fraction=0.01, reason="x")
    _tally_edge(seen, _market(), intent, "bet", st, sc)
    _tally_edge(seen, _market(Sport.TENNIS), None, "yes edge +0.0450 vs the 0.050 bar: edge 0.045 < min 0.05", st, sc)
    b = seen["baseball"]
    assert b == {"best": 0.041, "priced": 4, "spread_blocked": 1, "within_1pt": 2, "bets": 1, "bar": 0.03}
    t = seen["tennis"]
    assert t["bar"] == 0.05 and t["best"] == 0.045 and t["within_1pt"] == 1


def test_runner_folds_cycles_into_one_daily_row_and_the_board_renders_it(tmp_path):
    r = Runner.__new__(Runner)
    r.store = Store(str(tmp_path / "t.sqlite"))
    r.account = "sim"
    r._record_edge_bar({"baseball": {"best": 0.012, "priced": 5, "spread_blocked": 2,
                                     "within_1pt": 0, "bets": 0, "bar": 0.03}})
    r._record_edge_bar({"baseball": {"best": 0.021, "priced": 6, "spread_blocked": 1,
                                     "within_1pt": 1, "bets": 0, "bar": 0.03},
                        "hockey": {"best": None, "priced": 3, "spread_blocked": 3,
                                   "within_1pt": 0, "bets": 0, "bar": 0.03}})
    r._record_edge_bar({})                                   # nothing priced: no row
    today = datetime.now(timezone.utc).date().isoformat()
    row = r.store.get_kv(f"edgebar:sim:{today}")
    assert row["baseball"] == {"best": 0.021, "priced": 11, "spread_blocked": 3, "within_1pt": 1,
                               "bets": 0, "cycles": 2, "bar": 0.03}
    assert row["hockey"]["best"] is None and row["hockey"]["cycles"] == 1
    rows = edge_bar_rows(r.store, "sim")
    assert {(x["date"], x["sport"]) for x in rows} == {(today, "baseball"), (today, "hockey")}
    assert edge_bar_rows(r.store, "real") == []
    html = _edge_bar(rows)
    assert "Distance" not in html and "+2.10" in html and "-0.90" in html and "Spread-blocked" in html
    assert _svg_edge_bar(rows) == ""                         # one day: no line yet
    # a second day draws the chart with the bar
    r.store.set_kv("edgebar:sim:2000-01-01", {"baseball": {"best": -0.004, "priced": 2,
                                                            "spread_blocked": 0, "within_1pt": 0,
                                                            "bets": 0, "cycles": 1, "bar": 0.03}})
    svg = _svg_edge_bar(edge_bar_rows(r.store, "sim"))
    assert svg.startswith("<svg") and "bar 3.0 pt" in svg and 'class="line edge"' in svg
    assert "Nothing priced yet" in _edge_bar([])


def test_kv_prefix_scan(tmp_path):
    s = Store(str(tmp_path / "t.sqlite"))
    s.set_kv("edgebar:sim:2026-10-13", {"a": 1})
    s.set_kv("edgebar:real:2026-10-13", {"b": 2})
    s.set_kv("other", 3)
    assert s.kv_prefix("edgebar:sim:") == {"edgebar:sim:2026-10-13": {"a": 1}}
    assert len(s.kv_prefix("edgebar:")) == 2 and s.kv_prefix("zzz") == {}


def test_locked_pairs_split_complete_sets_from_naked_fills():
    orders = [
        {"market_id": "m1", "side": "yes", "price": 0.44, "filled": 2.0},
        {"market_id": "m1", "side": "no", "price": 0.53, "filled": 1.0},    # 1 pair at 1-0.97=+0.03
        {"market_id": "m2", "side": "yes", "price": 0.60, "filled": 3.0},   # naked
        {"market_id": "m3", "side": "no", "price": 0.50, "filled": 0.0},    # never filled
    ]
    p = locked_pairs(orders)
    assert p["markets"] == 2 and p["markets_paired"] == 1
    assert p["paired_contracts"] == 1.0 and p["naked_yes_contracts"] == 4.0 and p["naked_no_contracts"] == 0.0
    assert p["locked_edge_per_contract"] == pytest.approx(0.03) and p["locked_dollars"] == pytest.approx(0.03)
    assert locked_pairs([])["markets"] == 0 and locked_pairs([])["locked_edge_per_contract"] is None


def test_maker_report_prints_the_pair_line(tmp_path):
    store = Store(str(tmp_path / "t.sqlite"))
    raw = {"arm": "baseball/touch/maker", "placed_at": "2026-10-13T00:00:00+00:00", "rested_seconds": 20.0}
    store.record_order(client_id="a", order_id="a", market_id="m1", side="yes", price=0.44, size=2.0,
                       filled=2.0, status="filled", raw={**raw, "tape_through": 2.0})
    store.record_order(client_id="b", order_id="b", market_id="m1", side="no", price=0.53, size=2.0,
                       filled=2.0, status="filled", raw={**raw, "tape_through": 2.0})
    rep = maker_report(store)
    assert rep["pairs"]["paired_contracts"] == 2.0 and rep["pairs"]["naked_yes_contracts"] == 0.0
    text = format_maker_report(rep)
    assert "locked pairs: 1 of 1 markets" in text and "+3.00 pt per contract" in text
