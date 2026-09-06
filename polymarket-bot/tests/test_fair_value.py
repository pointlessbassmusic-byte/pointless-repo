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
