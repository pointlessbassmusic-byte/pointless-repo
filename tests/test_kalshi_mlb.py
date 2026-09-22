"""Kalshi MLB pairing: opponents, home field, and the YES-side restatement."""

from sportsbot.core.types import Exchange, MarketInfo, Sport
from sportsbot.exchanges.kalshi import (
    KALSHI_MLB_TEAMS,
    KalshiClient,
    split_mlb_event,
)


def _mkt(ticker: str, event: str, team: str, code: str) -> MarketInfo:
    return MarketInfo(
        exchange=Exchange.KALSHI, market_id=ticker, question=f"{team} wins",
        slug=ticker, sport=Sport.BASEBALL,
        home=team, away=team,          # Kalshi repeats the same team
        meta={"event_ticker": event, "team_code": code})


def test_every_team_code_maps_to_a_real_rating_key():
    """A code that maps to nothing silently drops that team's games; a code
    that maps to the wrong name bets on the wrong team."""
    assert len(KALSHI_MLB_TEAMS) == 30
    assert len(set(KALSHI_MLB_TEAMS.values())) == 30
    assert KALSHI_MLB_TEAMS["LAD"] == "los angeles dodgers"
    assert KALSHI_MLB_TEAMS["LAA"] == "los angeles angels"
    assert KALSHI_MLB_TEAMS["CWS"] == "chicago white sox"
    assert KALSHI_MLB_TEAMS["CHC"] == "chicago cubs"


def test_event_split_reads_away_then_home_past_the_date_prefix():
    """The ticker tail carries a date/time before the codes, and the order is
    away+home — reversing it hands home advantage to the wrong team."""
    assert split_mlb_event("KXMLBGAME-26SEP242210SDLAD", {"SD", "LAD"}) == ("SD", "LAD")
    assert split_mlb_event("KXMLBGAME-26SEP242140LAASEA", {"LAA", "SEA"}) == ("LAA", "SEA")
    # ambiguous or unrecognised -> None, never a guess
    assert split_mlb_event("KXMLBGAME-26SEP24XXXX", {"SD", "LAD"}) is None


def test_pairing_fills_the_opponent_from_the_sibling_market():
    """Kalshi sets no_sub_title to the SAME team as yes_sub_title, so each
    market looks like a game against itself and the scanner drops it."""
    ev = "KXMLBGAME-26SEP242210SDLAD"
    out = KalshiClient._pair_event_opponents([
        _mkt(f"{ev}-SD", ev, "San Diego", "SD"),
        _mkt(f"{ev}-LAD", ev, "Los Angeles D", "LAD"),
    ], Sport.BASEBALL)
    assert len(out) == 2
    by_id = {m.market_id: m for m in out}
    sd = by_id[f"{ev}-SD"]
    assert sd.home == "san diego padres"          # YES side stays `home`
    assert sd.away == "los angeles dodgers"
    assert sd.meta["home_field"] == "los angeles dodgers"   # true home field
    lad = by_id[f"{ev}-LAD"]
    assert lad.home == "los angeles dodgers" and lad.away == "san diego padres"
    assert lad.meta["home_field"] == "los angeles dodgers"


def test_unpaired_or_unmapped_markets_are_dropped_not_guessed():
    ev = "KXMLBGAME-26SEP242210SDLAD"
    one_sided = [_mkt(f"{ev}-SD", ev, "San Diego", "SD")]
    assert KalshiClient._pair_event_opponents(one_sided, Sport.BASEBALL) == []
    unmapped = [_mkt(f"{ev}-SD", ev, "San Diego", "SD"),
                _mkt(f"{ev}-ZZZ", ev, "Nowhere", "ZZZ")]
    assert KalshiClient._pair_event_opponents(unmapped, Sport.BASEBALL) == []


def test_scanner_models_the_true_home_team_and_restates_for_the_yes_side(tmp_path):
    """Half of Kalshi's markets have the VISITOR as the YES side. The model
    must still put home advantage on the home team, and prob_yes must still
    mean P(MarketInfo.home wins)."""
    from sportsbot.bot.scanner import Scanner
    from sportsbot.engine.baseball import BaseballModel

    model = BaseballModel()
    model.elo.ratings.clear()
    for team in ("los angeles dodgers", "san diego padres"):
        model.elo.get(team)                      # equal ratings, 1500 each
    scanner = Scanner({Sport.BASEBALL: model})

    ev = "KXMLBGAME-26SEP242210SDLAD"
    markets = KalshiClient._pair_event_opponents([
        _mkt(f"{ev}-SD", ev, "San Diego", "SD"),
        _mkt(f"{ev}-LAD", ev, "Los Angeles D", "LAD"),
    ], Sport.BASEBALL)
    scanned = {s.market.market_id: s for s in scanner.scan(markets)}
    assert len(scanned) == 2

    p_lad = scanned[f"{ev}-LAD"].prediction.prob_yes   # Dodgers = home field
    p_sd = scanned[f"{ev}-SD"].prediction.prob_yes     # Padres = visitor
    # Equal Elo, so the only asymmetry is home advantage: the home team is
    # favoured, the visitor is not, and the two sides sum to one.
    assert p_lad > 0.5 > p_sd
    assert abs((p_lad + p_sd) - 1.0) < 1e-9


def test_tennis_markets_pair_on_player_names_without_home_field():
    """Tennis has the same one-market-per-competitor shape, but no home
    advantage — so it pairs on names and must NOT invent a home field."""
    ev = "KXATPMATCH-26SEP23BASCIN"
    mkts = [
        MarketInfo(exchange=Exchange.KALSHI, market_id=f"{ev}-CIN", slug=f"{ev}-CIN",
                   question="Federico Cina wins", sport=Sport.TENNIS,
                   home="Federico Cina", away="Federico Cina",
                   meta={"event_ticker": ev}),
        MarketInfo(exchange=Exchange.KALSHI, market_id=f"{ev}-BAS", slug=f"{ev}-BAS",
                   question="Nikoloz Basilashvili wins", sport=Sport.TENNIS,
                   home="Nikoloz Basilashvili", away="Nikoloz Basilashvili",
                   meta={"event_ticker": ev}),
    ]
    out = KalshiClient._pair_event_opponents(mkts, Sport.TENNIS)
    assert len(out) == 2
    cina = next(m for m in out if m.market_id.endswith("-CIN"))
    assert cina.home == "Federico Cina"
    assert cina.away == "Nikoloz Basilashvili"
    assert "home_field" not in cina.meta
