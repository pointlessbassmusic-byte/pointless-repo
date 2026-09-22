"""cryptoarb invariants — the properties that keep this from becoming the
dashboard the archive warns about ("overstated performance: BOOTSTRAP_TRADES
+ zero fees")."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cryptoarb"))

import allocator  # noqa: E402
import engine as eng  # noqa: E402
import strategies as strat  # noqa: E402
from broker import PaperBroker  # noqa: E402
from fees import DEFAULT_TAKER, FeeBook  # noqa: E402
from venues import Top  # noqa: E402


def opp(net_bps, notional=1000.0, strategy="bundle"):
    return strat.Opportunity(strategy=strategy, label="t", gross_bps=net_bps,
                             fee_bps=0.0, other_cost_bps=0.0, net_bps=net_bps,
                             max_notional=notional)


# ---------------------------------------------------------------- broker

def test_negative_edge_is_never_filled():
    b = PaperBroker()
    for bad in (-0.1, -10.0, -50.0):
        assert b.execute(opp(bad), 50.0, "t") is None
    assert b.cash == 100.0 and not b.fills


def test_fill_is_capped_by_depth_and_cash():
    b = PaperBroker()
    f = b.execute(opp(100.0, notional=7.0), 50.0, "t")   # book only has $7
    assert f.notional == 7.0
    b2 = PaperBroker(starting_cash=3.0, cash=3.0)
    f2 = b2.execute(opp(100.0, notional=1000.0), 50.0, "t")
    assert f2.notional == 3.0                             # never exceeds cash


def test_pnl_is_net_of_fees():
    b = PaperBroker()
    f = b.execute(opp(100.0, notional=100.0), 100.0, "t")  # +100bps on $100
    assert abs(f.pnl - 1.0) < 1e-9 and abs(b.cash - 101.0) < 1e-9


# ---------------------------------------------------------------- fees

def test_unknown_venue_never_prices_as_free():
    fb = FeeBook()
    assert fb.taker_rate("madeup") == max(DEFAULT_TAKER.values())
    assert fb.round_trip_bps("kraken", "kucoin") == 50.0


# ------------------------------------------------------------ strategies

def test_cross_exchange_subtracts_fees_and_depeg_risk():
    fb = FeeBook()
    snap = {"BTC": {
        "kraken": Top("kraken", "XBTUSD", 100.0, 100.01, 5.0, 5.0),
        "kucoin": Top("kucoin", "BTC-USDT", 100.02, 100.03, 5.0, 5.0)}}
    o = strat.cross_exchange(snap, fb)[0]
    assert o.gross_bps > 0                       # raw dislocation exists
    assert o.net_bps < 0 and not o.tradeable     # but fees eat it
    assert o.fee_bps == 50.0
    assert o.other_cost_bps == strat.CROSS_QUOTE_RISK_BPS


def test_bundle_edge_is_one_dollar_minus_both_asks():
    fb = FeeBook()
    books = [{"market_id": "m", "question": "q", "asks": [0.48, 0.49],
              "sizes": [100.0, 100.0]}]
    o = strat.bundle(books, fb)[0]
    assert abs(o.gross_bps - 300.0) < 1e-6       # $1 - 0.97 = 3c = 300bps
    assert o.tradeable
    over = strat.bundle([{"market_id": "m", "question": "q",
                          "asks": [0.52, 0.49], "sizes": [1.0, 1.0]}], fb)[0]
    assert not over.tradeable                    # 1.01 > $1: no edge


def test_crossed_or_one_sided_book_is_unusable():
    assert not Top("x", "s", 100.0, 99.0).ok     # crossed
    assert not Top("x", "s", 0.0, 99.0).ok       # one-sided


# ------------------------------------------------------------- allocator

def test_losses_only_ever_shrink_allocation():
    base = allocator.allocations(100.0, {})
    for k in allocator.STRUCTURAL_PRIOR:
        for loss in (-0.5, -5.0, -50.0):
            assert allocator.allocations(100.0, {k: loss})[k] <= base[k]
        # a win never inflates past the structural prior
        assert allocator.allocations(100.0, {k: 999.0})[k] == base[k]


def test_allocation_respects_cap_and_floor():
    caps = {"bundle": 50.0}
    assert allocator.size_for(opp(10.0), caps, {"bundle": 48.0}, 100.0) == 0.0
    assert allocator.size_for(opp(10.0, 12.0), caps, {}, 100.0) == 12.0


# ------------------------------------------------------------- live gate

def test_live_gate_never_arms(monkeypatch):
    monkeypatch.setenv("CRYPTOARB_LIVE", "1")
    for v in ("COINBASE", "KRAKEN", "KUCOIN"):
        monkeypatch.setenv(f"{v}_API_KEY", "x")
        monkeypatch.setenv(f"{v}_API_SECRET", "x")
    monkeypatch.setenv("KUCOIN_API_PASSPHRASE", "x")
    g = eng.live_gate({"mode": "real"})
    assert g["mode_is_real"] and g["env_armed"] and all(g["keys_present"].values())
    assert not g["armed"]                        # executor still missing
    assert "live executor not implemented" in g["blocked_by"]
