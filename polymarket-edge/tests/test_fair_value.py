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


def test_place_limit_buy_uses_polymarket_client_sdk(monkeypatch):
    from src.clients import clob as clob_mod

    placed = {}

    class FakeSecureClient:
        @classmethod
        def create(cls, **kwargs):
            placed["create_kwargs"] = kwargs
            return cls()

        def place_limit_order(self, **kwargs):
            placed["order_kwargs"] = kwargs
            return {"order_id": "ord-123", "status": "live"}

    monkeypatch.setattr(clob_mod, "_import_secure_client", lambda: FakeSecureClient)
    c = clob_mod.ClobClient(private_key="0xkey", funder="0xwallet")
    resp = c.place_limit_buy("tok1", 0.4567, 12.345)
    assert resp["success"] and resp["orderID"] == "ord-123" and resp["errorMsg"] == ""
    assert placed["create_kwargs"] == {"private_key": "0xkey", "wallet": "0xwallet"}
    assert placed["order_kwargs"] == {"token_id": "tok1", "side": "BUY",
                                      "price": 0.457, "size": 12.35}


def test_trader_requires_private_key():
    import pytest
    from src.clients.clob import ClobClient
    with pytest.raises(RuntimeError, match="POLYMARKET_PRIVATE_KEY"):
        ClobClient()._get_trader()


def test_weather_question_parser_dialects():
    from src.models.weather import parse_question

    q = parse_question("Will the highest temperature in New York City be between 72-73°F on September 17?", 2026)
    assert (q.city, q.unit, q.floor, q.cap) == ("new york city", "fahrenheit", 72.0, 73.0)
    assert q.target.isoformat() == "2026-09-17"

    q = parse_question("Will the highest temperature in Miami be 77°F or below on September 17?", 2026)
    assert (q.floor, q.cap) == (None, 78.0)   # T <= 77

    q = parse_question("Will the highest temperature in Wuhan be 21°C on September 17?", 2026)
    assert (q.unit, q.floor, q.cap) == ("celsius", 21.0, 21.0)

    q = parse_question("Will the highest temperature in London be 30°C or above on September 17?", 2026)
    assert (q.floor, q.cap) == (29.0, None)   # T >= 30

    assert parse_question("Will the Chiefs beat the Bills?", 2026) is None


def test_weather_model_prices_band_market():
    import time as _time
    from src.models.weather import WeatherModel

    target = datetime.now(timezone.utc) + timedelta(days=1)
    mkt = make_market(
        "Will the highest temperature in Miami be between 88-89°F on "
        f"{target.strftime('%B')} {target.day}?",
        ["Yes", "No"], ["t-yes", "t-no"], None)
    mkt.end_date = target
    mkt.outcome_prices = [0.5, 0.5]

    model = WeatherModel({"sigma_base_f": 1.8, "sigma_per_day_f": 0.0,
                          "blend_market_weight": 0.0})
    model._cache["miami"] = (_time.monotonic(), {target.date().isoformat(): 88.5}, 0)
    ests = {e.outcome_name: e for e in model.estimate([mkt])}
    assert 0.40 < ests["Yes"].consensus_prob < 0.45   # dead-center 2F band
    assert abs(ests["Yes"].consensus_prob + ests["No"].consensus_prob - 1.0) < 1e-9


def test_city_bias_shifts_forecast():
    import time as _time
    from src.models.weather import WeatherModel

    target = datetime.now(timezone.utc) + timedelta(days=1)
    mkt = make_market(
        "Will the highest temperature in Miami be between 90-91°F on "
        f"{target.strftime('%B')} {target.day}?",
        ["Yes", "No"], ["t-yes", "t-no"], None)
    mkt.end_date = target
    mkt.outcome_prices = [0.5, 0.5]

    def prob(bias_cfg):
        m = WeatherModel({"sigma_base_f": 1.8, "sigma_per_day_f": 0.0,
                          "blend_market_weight": 0.0, "city_bias": bias_cfg})
        m._cache["miami"] = (_time.monotonic(), {target.date().isoformat(): 86.0}, 0)
        return {e.outcome_name: e for e in m.estimate([mkt])}["Yes"].consensus_prob

    # +4.5F bias moves the corrected forecast onto the 90-91 band
    assert prob({"miami": 4.5}) > prob({}) * 3


def test_weather_bias_report_from_resolved_estimates(tmp_path):
    from src.report import weather_bias
    from src.storage.db import Database

    db = Database(tmp_path / "t.db")
    db.conn.execute(
        "INSERT INTO estimates (ts, token_id, outcome, matched_game, fair_prob)"
        " VALUES ('2026-09-17','tokA','Yes',"
        "'weather:miami 2026-09-17 [88.0,89.0] mu=84.9±2.4',0.1)")
    db.conn.execute(  # open-ended band: censored even when YES
        "INSERT INTO estimates (ts, token_id, outcome, matched_game, fair_prob)"
        " VALUES ('2026-09-17','tokB','Yes',"
        "'weather:london 2026-09-17 [None,16.0] mu=14.0±1.3',0.8)")
    db.conn.commit()
    db.record_settlements({"tokA": 1.0, "tokB": 1.0})
    # observed = 88.5 midpoint; error vs mu 84.9 = +3.6
    assert weather_bias(db) == [("miami", 1, 3.6)]


def _weather_market_for(local_date):
    mkt = make_market(
        "Will the highest temperature in Miami be between 88-89°F on "
        f"{local_date.strftime('%B')} {local_date.day}?",
        ["Yes", "No"], ["t-yes", "t-no"], None)
    mkt.end_date = datetime(local_date.year, local_date.month, local_date.day,
                            tzinfo=timezone.utc)
    mkt.outcome_prices = [0.5, 0.5]
    return mkt


def _model_at_local_hour(hour, day_delta=0, forecast=88.5):
    """WeatherModel whose Miami cache carries a UTC offset putting the city's
    local clock at `hour`, with a forecast day_delta days from that local date."""
    import time as _time
    from src.models.weather import WeatherModel

    now = datetime.now(timezone.utc)
    offset = int((hour - now.hour) * 3600 - now.minute * 60 - now.second)
    local_date = (now + timedelta(seconds=offset)).date() + timedelta(days=day_delta)
    m = WeatherModel({"sigma_base_f": 1.8, "blend_market_weight": 0.0})
    m._cache["miami"] = (_time.monotonic(), {local_date.isoformat(): forecast}, offset)
    return m, local_date


def test_weather_abstains_once_the_days_high_is_realized():
    """Past late afternoon the book prices the observed high while the model
    still holds a forecast — the market's information strictly dominates, so
    the model has no business quoting that day."""
    model, local_date = _model_at_local_hour(12)
    assert model.estimate([_weather_market_for(local_date)])       # midday: in play

    model, local_date = _model_at_local_hour(20)
    assert model.estimate([_weather_market_for(local_date)]) == []

    model, local_date = _model_at_local_hour(9, day_delta=-1)      # yesterday
    assert model.estimate([_weather_market_for(local_date)]) == []
