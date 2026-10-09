"""NBA / NFL / NHL under the sharp anchor: the venues label a team three
different ways and the matcher refuses anything under 0.85, so the
canonical name table is what makes these markets priceable at all."""

from datetime import datetime, timedelta, timezone

import pytest

from sportsbot.core.types import Exchange, MarketInfo, Sport
from sportsbot.data import teams


def test_canonical_resolves_code_nickname_city_and_full_name_per_league():
    B, F, H = Sport.BASKETBALL, Sport.FOOTBALL, Sport.HOCKEY
    assert teams.canonical(B, "Grizzlies") == "Memphis Grizzlies"
    assert teams.canonical(B, "MEM") == "Memphis Grizzlies"
    assert teams.canonical(B, "Memphis") == "Memphis Grizzlies"
    assert teams.canonical(B, "LA Clippers") == "Los Angeles Clippers"
    assert teams.canonical(B, "Trail Blazers") == "Portland Trail Blazers"
    assert teams.canonical(F, "49ers") == "San Francisco 49ers"
    assert teams.canonical(H, "Montréal Canadiens") == "Montreal Canadiens"
    assert teams.canonical(H, "Utah Hockey Club") == "Utah Mammoth"
    # the same nickname is a different team per league, never a guess across
    assert teams.canonical(H, "Kings") == "Los Angeles Kings"
    assert teams.canonical(B, "Kings") == "Sacramento Kings"
    assert teams.canonical(F, "Panthers") == "Carolina Panthers"
    assert teams.canonical(H, "Panthers") == "Florida Panthers"


def test_canonical_refuses_ambiguity_and_unknowns():
    assert teams.canonical(Sport.BASKETBALL, "Los Angeles") is None     # Lakers or Clippers
    assert teams.canonical(Sport.FOOTBALL, "New York") is None          # Giants or Jets
    assert teams.canonical(Sport.BASKETBALL, "Nonsense") is None
    assert teams.canonical(Sport.TENNIS, "Grizzlies") is None            # not a team sport
    assert teams.canonical(None, "Grizzlies") is None
    assert teams.canonical_for_odds_key("tennis_atp_shanghai", "Jannik Sinner") == "Jannik Sinner"
    assert teams.canonical_for_odds_key("basketball_nba", "LA Clippers") == "Los Angeles Clippers"


def test_polymarket_team_markets_are_canonicalised_and_futures_excluded():
    from sportsbot.exchanges.polymarket import PolymarketClient

    parse = PolymarketClient()._moneyline_markets_from_event
    start = (datetime.now(timezone.utc) + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S+00")
    ev = {"slug": "nba-mem-chi-2026-10-09", "markets": [
        {"sportsMarketType": "moneyline", "conditionId": "c1", "gameStartTime": start,
         "outcomes": '["Grizzlies","Bulls"]', "clobTokenIds": '["a","b"]',
         "feeSchedule": {"rate": 0.05, "rebateRate": 0.15},
         "acceptingOrders": True, "enableOrderBook": True},
        {"sportsMarketType": "basketball_team_to_score_first", "conditionId": "c2",
         "gameStartTime": start, "outcomes": '["Grizzlies","Bulls"]',
         "clobTokenIds": '["c","d"]', "acceptingOrders": True, "enableOrderBook": True}]}
    (m,) = parse(ev, Sport.BASKETBALL)
    assert (m.home, m.away) == ("Memphis Grizzlies", "Chicago Bulls")
    assert m.meta["outcome_names"] == ["Grizzlies", "Bulls"]
    assert m.meta["fee_rate"] == 0.05 and "home_field" not in m.meta
    future = {"slug": "nba-2027-champion", "markets": [
        {"conditionId": "f1", "outcomes": '["Yes","No"]', "clobTokenIds": '["y","n"]',
         "feeSchedule": {"rate": 0.03}, "acceptingOrders": True, "enableOrderBook": True}]}
    assert parse(future, Sport.BASKETBALL) == []
    unknown = {**ev, "markets": [{**ev["markets"][0], "outcomes": '["Grizzlies","Martians"]'}]}
    assert parse(unknown, Sport.BASKETBALL) == []                     # unknown team: skip


def test_kalshi_team_markets_pair_by_code_and_keep_the_home_side():
    from sportsbot.exchanges.kalshi import KalshiClient

    def mk(ticker, city):
        return MarketInfo(exchange=Exchange.KALSHI, market_id=ticker, slug=ticker,
                          sport=Sport.BASKETBALL, home=city, away=city,
                          meta={"event_ticker": ticker.rsplit("-", 1)[0],
                                "team_code": ticker.rsplit("-", 1)[-1]})
    group = [mk("KXNBAGAME-26OCT09MEMCHI-MEM", "Memphis"),
             mk("KXNBAGAME-26OCT09MEMCHI-CHI", "Chicago")]
    out = KalshiClient._pair_event_opponents(group, Sport.BASKETBALL)
    assert len(out) == 1
    (m,) = out
    assert m.home == "Chicago Bulls" and m.away == "Memphis Grizzlies"   # kept ticker = home side
    assert m.meta["home_field"] == "Chicago Bulls"
    assert m.start_time is None                                       # ticker has no time
    # an unmapped code is skipped, never guessed
    bad = [mk("KXNBAGAME-26OCT09XXXCHI-XXX", "Nowhere"), mk("KXNBAGAME-26OCT09XXXCHI-CHI", "Chicago")]
    assert KalshiClient._pair_event_opponents(bad, Sport.BASKETBALL) == []


def test_sharp_collector_canonicalises_book_names_for_team_sports():
    from sportsbot.signals import sharp as sh

    payload = [{"id": "e1", "sport_key": "basketball_nba",
                "commence_time": "2026-10-10T00:00:00Z",
                "home_team": "Chicago Bulls", "away_team": "LA Clippers",
                "bookmakers": [{"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Chicago Bulls", "price": 1.9}, {"name": "LA Clippers", "price": 2.0}]}]}]}]
    (ev,) = sh.parse_events(payload)
    assert ev.away_team == "Los Angeles Clippers" and ev.home_team == "Chicago Bulls"
    assert "pinnacle" in ev.books
    assert sh.STATIC_SPORT_KEYS[Sport.BASKETBALL] == ("basketball_nba",)
    assert sh.STATIC_SPORT_KEYS[Sport.FOOTBALL] == ("americanfootball_nfl",)
    assert sh.STATIC_SPORT_KEYS[Sport.HOCKEY] == ("icehockey_nhl",)


def test_sharp_only_sports_need_the_sharp_signal_and_appear_as_arms(tmp_path):
    from sportsbot.bot.portfolio import running_arms_for
    from sportsbot.bot.runner import load_models
    from sportsbot.data.store import Store
    from sportsbot.engine.sharpline import SharpLineModel

    store = Store(str(tmp_path / "s.sqlite"))
    cfg = {"sports": {"basketball": {"enabled": True, "signal": "sharp"},
                      "tennis": {"enabled": False}, "baseball": {"enabled": False},
                      "table_tennis": {"enabled": False}}}
    models = load_models(cfg, str(tmp_path), store=store)
    assert isinstance(models[Sport.BASKETBALL], SharpLineModel)
    assert Sport.FOOTBALL not in models                     # team sports are opt-in
    with pytest.raises(ValueError):
        load_models({"sports": {"basketball": {"enabled": True}}}, str(tmp_path), store=store)
    arms = running_arms_for({"sports": {"hockey": {"enabled": True, "signal": "sharp"}},
                             "execution": {"post_inside_spread": True}})
    assert arms == {"hockey/sharp/maker", "hockey/sharp/taker"}


def test_shipped_configs_enable_team_sports_on_the_sharp_signal():
    import yaml

    from sportsbot.bot.portfolio import load_allocation, running_arms_for

    for path in ("config/default.yaml", "config/sim.yaml"):
        cfg = yaml.safe_load(open(path))
        for key in ("basketball", "football", "hockey"):
            assert cfg["sports"][key]["signal"] == "sharp", (path, key)
            assert key in cfg["scan"]["sports"], (path, key)
        assert {"basketball/sharp/maker", "football/sharp/taker", "hockey/sharp/maker"} <= running_arms_for(cfg)
    alloc = load_allocation("config/allocation.yaml")
    assert "basketball/sharp/maker" in alloc.arms and alloc.setting("hockey/sharp/taker").learn is False


def test_scanner_adopts_the_sharp_books_start_when_the_venue_has_none(tmp_path):
    from sportsbot.bot.scanner import Scanner
    from sportsbot.data.store import Store
    from sportsbot.engine.sharpline import SharpLineModel

    store = Store(str(tmp_path / "s.sqlite"))
    now = datetime.now(timezone.utc)
    start = now + timedelta(hours=7)
    store.record_sharp_quotes([{
        "sport_key": "basketball_nba", "event_id": "e1", "commence_time": start.isoformat(),
        "home_team": "Chicago Bulls", "away_team": "Memphis Grizzlies", "bookmaker": "pinnacle",
        "home_implied": 0.57, "away_implied": 0.47, "home_fair": 0.55, "away_fair": 0.45,
        "overround": 0.04}], ts=now.isoformat())
    sc = Scanner({Sport.BASKETBALL: SharpLineModel(store, Sport.BASKETBALL)})
    m = MarketInfo(exchange=Exchange.KALSHI, market_id="KXNBAGAME-26OCT09MEMCHI-CHI",
                   sport=Sport.BASKETBALL, home="Chicago Bulls", away="Memphis Grizzlies",
                   start_time=None)
    scanned, drops = sc.scan_verbose([m])
    assert drops == [] and len(scanned) == 1
    assert scanned[0].prediction.prob_yes == pytest.approx(0.55)
    assert scanned[0].market.start_time is not None
    assert abs((scanned[0].market.start_time - start).total_seconds()) < 1
    # a venue start is never overwritten
    m2 = m.model_copy(update={"start_time": now + timedelta(hours=9)})
    (s2,), _ = sc.scan_verbose([m2])
    assert s2.market.start_time == now + timedelta(hours=9)


def test_sharp_model_without_lines_is_reported_as_such_not_as_unfit(tmp_path):
    from sportsbot.bot.scanner import Scanner
    from sportsbot.data.store import Store
    from sportsbot.engine.sharpline import SharpLineModel

    sc = Scanner({Sport.HOCKEY: SharpLineModel(Store(str(tmp_path / "s.sqlite")), Sport.HOCKEY)})
    m = MarketInfo(exchange=Exchange.POLYMARKET, market_id="c", sport=Sport.HOCKEY,
                   home="Boston Bruins", away="Buffalo Sabres")
    _, drops = sc.scan_verbose([m])
    assert len(drops) == 1 and drops[0].category == "no sharp line"
    assert "ODDS_API_KEY" in drops[0].reason and "fit" not in drops[0].reason


def test_allocation_parks_nothing_on_a_sport_nothing_can_price():
    from sportsbot.bot.portfolio import AllocationConfig, allocate_arms

    cfg = {"bankroll": {"max_total_exposure": 0.5, "max_fraction_per_sport": 0.2},
           "sports": {}, "execution": {}}
    running = {"baseball/model/maker", "baseball/model/taker",
               "hockey/sharp/maker", "hockey/sharp/taker"}
    port = allocate_arms(100.0, cfg, AllocationConfig(), [], running, mode="paper",
                         priceable_sports={"baseball"})
    assert port["sport_budget"]["hockey"] == 0.0 and port["sport_budget"]["baseball"] == 20.0
    assert "nothing can price" in port["arms"]["hockey/sharp/maker"]["source"]
    assert port["style_by_sport"]["hockey"] == "none"
    both = allocate_arms(100.0, cfg, AllocationConfig(), [], running, mode="paper",
                         priceable_sports={"baseball", "hockey"})
    assert both["sport_budget"] == {"baseball": 20.0, "hockey": 20.0}
