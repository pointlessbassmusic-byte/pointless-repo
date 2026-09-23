"""Trading dashboard: allocation policy, ledger arithmetic, gate, rendering."""

import pathlib
import sqlite3

import pytest

from sportsbot.bot.allocation import CLV_CAP, CLV_FLOOR, allocate, clv_multiplier
from sportsbot.bot.gate import MIN_SETTLED_BETS, evidence_gate
from sportsbot.bot.ledger import account_equity
from sportsbot.data.store import Store

CFG = {
    "sports": {"tennis": {"enabled": True}, "baseball": {"enabled": True},
               "table_tennis": {"enabled": True}},
    "bankroll": {"max_fraction_per_sport": 0.20, "max_total_exposure": 0.50},
    "adaptive": {"clv_min_bets": 30},
    "storage": {"ratings_dir": "data/ratings"},
}
RATED = {"tennis": True, "baseball": True, "table_tennis": True}


def _store(tmp_path):
    return Store(str(tmp_path / "s.sqlite"))


# ------------------------------------------------------------- allocation
def test_sleeve_without_ratings_gets_no_money():
    """Tennis has the best model in the repo and no fitted ratings on this
    host — a sophisticated model that cannot price anything gets zero."""
    a = allocate(100.0, CFG, {}, {**RATED, "tennis": False})
    assert a["sleeves"]["tennis"]["budget"] == 0.0
    assert a["sleeves"]["tennis"]["active"] is False
    assert "fit" in a["sleeves"]["tennis"]["bound_by"]
    assert a["sleeves"]["baseball"]["budget"] > 0


def test_allocation_never_exceeds_the_configured_sport_cap():
    a = allocate(100.0, CFG, {}, RATED)
    for sleeve in a["sleeves"].values():
        assert sleeve["budget"] <= 100.0 * CFG["bankroll"]["max_fraction_per_sport"] + 1e-9
    assert a["allocated"] <= 100.0 * CFG["bankroll"]["max_total_exposure"] + 1e-9


def test_weather_is_excluded_with_its_reason():
    """The market scores 0.0751 against our 0.1828 — there is nothing to bet."""
    a = allocate(100.0, CFG, {}, RATED)
    assert "weather" not in a["sleeves"]
    assert "no edge" in a["excluded"]["weather"]


def test_thin_clv_evidence_cannot_boost_a_sleeve():
    """mean_clv is averaged only over rows carrying a closing price, so the
    guard must count THOSE, not settled bets: 40 settled with 5 closing prices
    is 5 observations and must not clear a 30-observation bar."""
    thin = allocate(100.0, CFG,
                    {"baseball": {"n": 40, "n_clv": 5, "mean_clv": 0.05}}, RATED)
    flat = allocate(100.0, CFG, {}, RATED)
    assert thin["sleeves"]["baseball"]["clv_multiplier"] == 1.0
    assert thin["sleeves"]["baseball"]["weight"] == flat["sleeves"]["baseball"]["weight"]

    thick = allocate(100.0, CFG,
                     {"baseball": {"n": 40, "n_clv": 40, "mean_clv": 0.05}}, RATED)
    assert thick["sleeves"]["baseball"]["clv_multiplier"] > 1.0


def test_clv_multiplier_ignores_thin_evidence_then_reacts():
    assert clv_multiplier(0.05, 5, 30) == 1.0       # too few observations
    assert clv_multiplier(None, 100, 30) == 1.0     # nothing measured
    assert clv_multiplier(0.02, 40, 30) > 1.0       # beating the close -> more
    assert clv_multiplier(-0.02, 40, 30) < 1.0      # behind the close -> less
    assert clv_multiplier(9.9, 40, 30) == CLV_CAP   # bounded both ways
    assert clv_multiplier(-9.9, 40, 30) == CLV_FLOOR


def test_losses_alone_never_raise_an_allocation():
    """Anti-martingale: a sleeve deep in the red but with unchanged CLV keeps
    exactly its prior weight — being down is never a reason to size up."""
    flat = allocate(100.0, CFG, {}, RATED)
    losing = allocate(100.0, CFG,
                      {"baseball": {"n": 50, "n_clv": 50, "pnl": -400.0,
                                    "mean_clv": None}},
                      RATED)
    assert losing["sleeves"]["baseball"]["budget"] == flat["sleeves"]["baseball"]["budget"]

    # and a sleeve losing money WITH negative CLV is cut, never increased
    bad_clv = allocate(100.0, CFG,
                       {"baseball": {"n": 50, "n_clv": 50, "pnl": -400.0,
                                     "mean_clv": -0.03}},
                       RATED)
    assert bad_clv["sleeves"]["baseball"]["weight"] < flat["sleeves"]["baseball"]["weight"]


# ----------------------------------------------------------------- ledger
def test_equity_tracks_realized_open_stake_and_the_mark(tmp_path):
    store = _store(tmp_path)
    bet_id = store.record_bet("m1", "baseball", "YES", 0.60, 0.50, 10.0, 20.0,
                              0.07, "polymarket", "paper")
    eq = account_equity(store, "sim", 100.0)
    assert eq["exposure"] == 10.0
    assert eq["cash"] == 90.0
    # no quote yet -> marked at entry, so equity is unchanged by opening a bet
    assert eq["equity"] == pytest.approx(100.0)

    store.snapshot_quote("m1", 0.60, 0.62)          # market moved our way
    eq = account_equity(store, "sim", 100.0)
    assert eq["unrealized_pnl"] > 0 and eq["equity"] > 100.0

    store.settle_bet(bet_id, outcome=1, pnl=10.0, closing_price=0.61)
    eq = account_equity(store, "sim", 100.0)
    assert eq["realized_pnl"] == 10.0
    assert eq["equity"] == pytest.approx(110.0)
    assert eq["exposure"] == 0.0 and eq["settled_bets"] == 1


def test_sim_and_real_books_never_mix(tmp_path):
    store = _store(tmp_path)
    store.record_bet("m1", "baseball", "YES", 0.6, 0.5, 10.0, 20.0, 0.07,
                     "polymarket", "paper")
    store.record_bet("m2", "baseball", "YES", 0.6, 0.5, 25.0, 50.0, 0.07,
                     "polymarket", "live")
    assert account_equity(store, "sim", 100.0)["exposure"] == 10.0
    assert account_equity(store, "real", 500.0)["exposure"] == 25.0


# ------------------------------------------------------------------- gate
def test_gate_blocks_until_every_criterion_is_met(tmp_path):
    store = _store(tmp_path)
    g = evidence_gate(store, "sim")
    assert g["ready"] is False
    assert [c["ok"] for c in g["criteria"]] == [False] * 4

    # an unmeasurable criterion is never quietly treated as satisfied
    fees = [c for c in g["criteria"] if c["name"] == "fees verified"][0]
    assert fees["value"] == "outstanding"
    store.set_kv("fees_verified", {"verified": True, "at": "2026-09-22T00:00:00"})
    g = evidence_gate(store, "sim")
    assert [c for c in g["criteria"] if c["name"] == "fees verified"][0]["ok"]
    assert g["ready"] is False          # the other three still bind
    assert MIN_SETTLED_BETS == 200


# --------------------------------------------------------------- rendering
def test_page_renders_both_books_and_offers_no_live_switch(tmp_path):
    from sportsbot.dashboard import build

    store = _store(tmp_path)
    store.record_decision("sim", "m1", "skip", sport="baseball", title="A vs B",
                          model_prob=0.61, market_prob=0.58,
                          reason="spread 0.080 wider than the 0.030 limit")
    store.record_equity("sim", 100.0, 0.0, 100.0, 0.0, 0)
    out = str(tmp_path / "board.html")
    res = build({**CFG, "mode": "paper", "exchange": "polymarket",
                 "accounts": {"sim": {"starting_balance": 100.0}}}, store, out)
    assert res["sim_equity"] == 100.0 and res["gate_ready"] is False
    html = open(out).read()
    assert "Sim money" in html and "Real money" in html
    assert "spread 0.080 wider" in html          # the pass is visible, with its reason
    assert "No real-money account connected" in html
    # the toggle is a view switch: no form, no script, nothing that could trade
    assert "<script" not in html and "<form" not in html
    assert "SPORTSBOT_LIVE=1" in html            # says how live is actually enabled


def test_decision_reasons_group_instead_of_repeating():
    from sportsbot.dashboard import _reason_key

    assert (_reason_key("spread 0.080 wider than the 0.030 limit — fills")
            == _reason_key("spread 0.94 wider than the 0.030 limit — fills"))
    assert _reason_key("model uncertainty 0.22 over 0.20 — not confident") \
        == "model not confident enough to price"
    assert _reason_key("risk veto: stale quote") == "vetoed by the risk layer"


def test_rated_counts_sees_through_an_empty_ratings_file(tmp_path):
    """A fit that fetched nothing still writes a file — count entities."""
    import json
    from sportsbot.dashboard import rated_counts

    d = tmp_path / "ratings"
    d.mkdir()
    (d / "tennis.json").write_text(json.dumps({"overall": {}, "surfaces": {}}))
    (d / "baseball.json").write_text(json.dumps({"elo": {"NYY": 1500}}))
    counts = rated_counts({"storage": {"ratings_dir": str(d)}})
    assert counts["tennis"] == 0 and counts["baseball"] == 1
    assert counts["table_tennis"] == 0          # file absent entirely


def test_store_prunes_the_decision_feed(tmp_path):
    store = _store(tmp_path)
    for i in range(40):
        store.record_decision("sim", f"m{i}", "skip", reason="x")
    store.prune_decisions(keep=10)
    rows = sqlite3.connect(str(tmp_path / "s.sqlite")).execute(
        "SELECT COUNT(*) FROM decisions").fetchone()[0]
    assert rows == 10


def test_runner_and_dashboard_agree_on_the_starting_balance():
    """The stored equity curve and the Equity tile must be anchored to the
    same number. bankroll.amount (the staking bankroll) and the account's
    starting balance are different settings and differ by 10x in the shipped
    configs — reading one in the runner and the other in the page would put a
    $900 step between the curve and the tile."""
    from sportsbot.dashboard import starting_balance

    cfg = {"bankroll": {"amount": 1000.0},
           "accounts": {"sim": {"starting_balance": 100.0},
                        "real": {"starting_balance": 0.0}}}
    assert starting_balance(cfg, "sim") == 100.0
    assert starting_balance(cfg, "real") == 0.0

    src = (pathlib.Path(__file__).resolve().parents[1]
           / "sportsbot" / "bot" / "runner.py").read_text()
    assert "starting_balance(cfg, self.account)" in src
    assert 'self.starting_balance = float(bank.get("amount"' not in src


def test_each_book_is_sized_by_its_own_record(tmp_path):
    """Paper CLV must never size the real book: simulated fills deciding how
    much real money moves is the whole failure mode."""
    from sportsbot.dashboard import collect

    store = _store(tmp_path)
    bid = store.record_bet("m1", "baseball", "YES", 0.6, 0.50, 10.0, 20.0,
                           0.07, "polymarket", "paper")
    store.settle_bet(bid, outcome=1, pnl=10.0, closing_price=0.90)
    data = collect({**CFG, "mode": "paper", "exchange": "polymarket",
                    "accounts": {"sim": {"starting_balance": 100.0},
                                 "real": {"starting_balance": 500.0}}}, store)
    sim = data["accounts"]["sim"]["allocation"]["sleeves"]["baseball"]
    real = data["accounts"]["real"]["allocation"]["sleeves"]["baseball"]
    assert sim["n"] == 1          # the paper bet counts for the paper book
    assert real["n"] == 0         # and not for the real one
    assert data["accounts"]["real"]["equity"]["realized_pnl"] == 0.0


def test_settled_bets_filters_mode_before_truncating(tmp_path):
    """Filtering a truncated page in Python drops this account's older bets
    once the table grows past the limit — a permanent equity divergence."""
    store = _store(tmp_path)
    for i in range(5):
        bid = store.record_bet(f"live{i}", "baseball", "YES", 0.6, 0.5, 5.0,
                               10.0, 0.07, "kalshi", "live")
        store.settle_bet(bid, outcome=1, pnl=5.0)
    for i in range(20):                       # newer paper rows crowd the page
        bid = store.record_bet(f"paper{i}", "baseball", "YES", 0.6, 0.5, 5.0,
                               10.0, 0.07, "polymarket", "paper")
        store.settle_bet(bid, outcome=0, pnl=-5.0)

    assert len(store.settled_bets(limit=3, mode="live")) == 3
    assert account_equity(store, "real", 100.0)["realized_pnl"] == 25.0


def test_bootstrap_ratings_get_nothing():
    """Measured, not assumed: on 1,561 settled matches the bootstrap Elo was
    worse than a coin against the price and its bets lost ~18%. A sleeve on
    those ratings is switched off, and the page says why."""
    from sportsbot.bot.allocation import PROVISIONAL_RATINGS_FACTOR, allocate

    assert PROVISIONAL_RATINGS_FACTOR == 0.0
    boot = allocate(100.0, CFG, {}, RATED, provisional={"tennis": True})
    t = boot["sleeves"]["tennis"]
    assert t["active"] is False and t["budget"] == 0.0
    assert "worse than a coin" in t["bound_by"]
    # nothing leaks to the other sleeves beyond the caps
    assert boot["sleeves"]["baseball"]["budget"] <= 100.0 * CFG["bankroll"]["max_fraction_per_sport"] + 1e-9


def test_provenance_is_read_from_the_ratings_file(tmp_path):
    import json

    from sportsbot.dashboard import ratings_provenance

    d = tmp_path / "ratings"
    d.mkdir()
    (d / "tennis.json").write_text(json.dumps(
        {"overall": {"a": 1}, "meta": {"source": "kalshi-bootstrap"}}))
    (d / "baseball.json").write_text(json.dumps(
        {"elo": {"NYY": 1500}, "meta": {"source": "sackmann"}}))
    prov = ratings_provenance({"storage": {"ratings_dir": str(d)}})
    assert prov["tennis"] is True and prov["baseball"] is False
