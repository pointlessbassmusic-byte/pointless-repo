from datetime import datetime, timedelta, timezone

from src.clients.clob import Quote
from src.clients.gamma import SportsMarket
from src.clients.odds_api import Game, OddsApiClient
from src.models.fair_value import FairEstimate, match_and_estimate
from src.strategy.edge import build_signals


def make_market(question, outcomes, token_ids, start):
    return SportsMarket(
        condition_id="c1", question=question, slug="s", end_date=None,
        liquidity=1000, volume=0, outcomes=outcomes,
        outcome_prices=[0.5] * len(outcomes), clob_token_ids=token_ids,
        event_title="", game_start=start,
    )


def test_yes_maps_to_subject_team_not_any_team():
    """'Will the Chiefs beat the Bills?' names both teams — Yes must get the
    *Chiefs'* win probability (the subject), not whichever team matches first."""
    start = datetime.now(timezone.utc) + timedelta(hours=24)
    books = [{"Buffalo Bills": 1.6, "Kansas City Chiefs": 2.6}] * 3
    game = Game(sport_key="americanfootball_nfl", home_team="Kansas City Chiefs",
                away_team="Buffalo Bills", commence_time=start, book_odds=books)
    mkt = make_market("Will the Chiefs beat the Bills?", ["Yes", "No"], ["t-yes", "t-no"], start)

    ests = {e.outcome_name: e for e in
            match_and_estimate([mkt], [game], min_books=3, blend_market_weight=0.0)}
    assert set(ests) == {"Yes", "No"}
    # Chiefs are the underdog at 2.6 decimal odds → P(Yes) well under 0.5
    assert ests["Yes"].fair_prob < 0.5
    assert abs(ests["Yes"].fair_prob + ests["No"].fair_prob - 1.0) < 1e-9
    assert ests["Yes"].n_books == 3


def _estimate(token="tok1", fair=0.60):
    mkt = make_market("Will X beat Y?", ["Yes"], [token], None)
    return FairEstimate(market=mkt, outcome_index=0, outcome_name="Yes",
                        fair_prob=fair, consensus_prob=fair, matched_game="Y @ X", n_books=4)


def test_build_signals_skips_already_placed_tokens():
    quotes = {"tok1": Quote(token_id="tok1", bid=0.48, ask=0.50)}
    cfg = {"min_liquidity": 100}
    assert build_signals([_estimate()], quotes, cfg)
    assert build_signals([_estimate()], quotes, cfg, exclude_tokens={"tok1"}) == []


def test_build_signals_counts_existing_exposure_against_cap():
    quotes = {"tok1": Quote(token_id="tok1", bid=0.48, ask=0.50)}
    cfg = {"min_liquidity": 100, "max_total_exposure": 250}
    assert build_signals([_estimate()], quotes, cfg, existing_exposure=249.0) == []


def test_odds_cache_reuses_within_ttl():
    import time
    c = OddsApiClient("some-key", cache_ttl_sec=3600)
    c._cache["americanfootball_nfl"] = (time.monotonic(), [])
    # served from cache — no HTTP call is attempted
    assert c.h2h_games("americanfootball_nfl") == []


def test_wide_spread_is_filtered():
    quotes = {"tok1": Quote(token_id="tok1", bid=0.20, ask=0.50)}
    cfg = {"min_liquidity": 100, "max_spread": 0.10}
    assert build_signals([_estimate(fair=0.90)], quotes, cfg) == []
    cfg["max_spread"] = 0.50
    assert build_signals([_estimate(fair=0.90)], quotes, cfg) != []


def test_settlements_roundtrip(tmp_path):
    from src.storage.db import Database

    db = Database(tmp_path / "t.db")
    db.conn.execute(
        "INSERT INTO estimates (ts, token_id, condition_id, fair_prob)"
        " VALUES ('2026-01-01', 'tok1', 'cond1', 0.6)"
    )
    db.conn.commit()
    assert db.unsettled_condition_ids() == ["cond1"]
    db.record_settlements({"tok1": 1.0})
    assert db.settled_outcomes() == {"tok1": 1.0}
    assert db.unsettled_condition_ids() == []


def test_risk_gate_kill_switch_and_daily_loss(tmp_path):
    from src.risk import RiskGate
    from src.storage.db import Database

    db = Database(tmp_path / "t.db")
    gate = RiskGate(db, {"max_daily_loss_usd": 10, "kill_switch_file": "KILL"}, tmp_path)
    assert gate.check() == (True, "")

    # bought 100 shares @ 0.40 live, token resolved to 0 today
    db.conn.execute(
        "INSERT INTO orders (ts, token_id, ask, shares, stake_usd, status)"
        " VALUES (datetime('now'), 'tokX', 0.40, 100, 40, 'placed')"
    )
    db.conn.commit()
    db.record_settlements({"tokX": 0.0})
    assert db.realized_pnl_today() == -40.0
    ok, reason = gate.check()
    assert not ok and "loss limit" in reason

    (tmp_path / "KILL").touch()
    ok, reason = gate.check()
    assert not ok and "kill switch" in reason


def test_best_game_wins_over_first_city_token_match():
    """'New York ... Boston ...' must not lock onto a different sport's game
    that shares city tokens; nickname matches dominate."""
    from src.clients.odds_api import Game
    start = datetime.now(timezone.utc) + timedelta(hours=24)
    nhl = Game(sport_key="icehockey_nhl", home_team="Boston Bruins",
               away_team="New York Rangers", commence_time=start,
               book_odds=[{"Boston Bruins": 1.5, "New York Rangers": 2.8}] * 3)
    mlb = Game(sport_key="baseball_mlb", home_team="Boston Red Sox",
               away_team="New York Yankees", commence_time=start,
               book_odds=[{"Boston Red Sox": 2.4, "New York Yankees": 1.65}] * 3)
    mkt = make_market("Will the New York Yankees beat the Boston Red Sox?",
                      ["Yes", "No"], ["t-yes", "t-no"], start)
    # NHL game listed first: old first-match logic would grab it via city tokens
    ests = {e.outcome_name: e for e in
            match_and_estimate([mkt], [nhl, mlb], min_books=3, blend_market_weight=0.0)}
    assert "Yankees" in ests["Yes"].matched_game or "Red Sox" in ests["Yes"].matched_game
    assert ests["Yes"].fair_prob > 0.5  # Yankees favored at 1.65


def test_negated_question_inverts_probability():
    start = datetime.now(timezone.utc) + timedelta(hours=24)
    books = [{"Buffalo Bills": 1.6, "Kansas City Chiefs": 2.6}] * 3
    game = Game(sport_key="americanfootball_nfl", home_team="Kansas City Chiefs",
                away_team="Buffalo Bills", commence_time=start, book_odds=books)
    mkt = make_market("Will the Bills lose to the Chiefs?", ["Yes", "No"],
                      ["t-yes", "t-no"], start)
    ests = {e.outcome_name: e for e in
            match_and_estimate([mkt], [game], min_books=3, blend_market_weight=0.0)}
    # Bills are favorites to WIN (~0.62), so "Bills lose" must be ~0.38
    assert ests["Yes"].fair_prob < 0.5


def test_one_sided_book_is_filtered():
    quotes = {"tok1": Quote(token_id="tok1", bid=None, ask=0.50)}
    assert build_signals([_estimate(fair=0.90)], quotes, {"min_liquidity": 100}) == []


def test_settled_positions_release_exposure(tmp_path):
    from src.storage.db import Database
    db = Database(tmp_path / "t.db")
    db.conn.execute(
        "INSERT INTO orders (ts, token_id, ask, shares, stake_usd, status)"
        " VALUES (datetime('now'), 'tokX', 0.40, 100, 40, 'placed')")
    db.conn.commit()
    assert db.live_exposure() == 40.0
    db.record_settlements({"tokX": 1.0})
    assert db.live_exposure() == 0.0
