"""Polymarket discovery for the touch-only sports: several leagues per key,
de-duplicated, and three-way (Yes/No) books shaped for the touch arm."""

from datetime import datetime, timedelta, timezone

from sportsbot.core.types import Sport
from sportsbot.exchanges.polymarket import SPORT_FOR_TAG, SPORT_TAGS, PolymarketClient


def _iso(hours: float) -> str:
    """A time relative to now: fixed dates turn these open games into finished ones."""
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(slug, tag, markets):
    return {"slug": slug, "title": slug, "startDate": _iso(-11), "markets": markets}


def _ml(cid, slug, outcomes, question, group=None, start=None):
    start = start or _iso(6)
    return {"conditionId": cid, "slug": slug, "question": question, "groupItemTitle": group,
            "sportsMarketType": "moneyline", "closed": False, "acceptingOrders": True,
            "enableOrderBook": True, "outcomes": outcomes, "clobTokenIds": '["t1", "t2"]',
            "gameStartTime": start, "endDate": _iso(11), "active": True,
            "orderPriceMinTickSize": 0.01, "orderMinSize": 5, "negRisk": True}


def test_touch_only_keys_map_to_their_sports_and_carry_lists_of_leagues():
    for key, sport in (("soccer", Sport.SOCCER), ("esports", Sport.ESPORTS), ("mma", Sport.MMA),
                       ("college_football", Sport.COLLEGE_FOOTBALL), ("cricket", Sport.CRICKET)):
        assert SPORT_FOR_TAG[key] is sport and isinstance(SPORT_TAGS[key], list) and SPORT_TAGS[key]
    assert SPORT_TAGS["soccer"][:2] == [100977, 306] and SPORT_TAGS["esports"][0] == 65
    assert SPORT_TAGS["table_tennis"] == 103767                # untouched


def test_multi_tag_discovery_dedupes_and_shapes_three_way_books(monkeypatch):
    calls = []

    def fake_get(self, url, params=None):
        calls.append(dict(params or {}))
        tag = params["tag_id"]
        if params.get("offset", 0) > 0:
            return []
        if tag == 100977:       # UCL: a three-way event
            return [_event("ucl-atm-mnu-2026-10-13", tag, [
                _ml("c-atm", "ucl-atm-mnu-2026-10-13-atm", '["Yes", "No"]',
                    "Will Club Atlético de Madrid win on 2026-10-13?", "Club Atlético de Madrid"),
                _ml("c-draw", "ucl-atm-mnu-2026-10-13-draw", '["Yes", "No"]',
                    "Will ... end in a draw?", "Draw (Club Atlético de Madrid vs. Manchester United FC)"),
                _ml("c-mnu", "ucl-atm-mnu-2026-10-13-mnu", '["Yes", "No"]',
                    "Will Manchester United FC win on 2026-10-13?", "Manchester United FC")])]
        if tag == 306:          # EPL page returns the same UCL event again (shared tag) + its own
            return [_event("ucl-atm-mnu-2026-10-13", tag, [
                        _ml("c-atm", "ucl-atm-mnu-2026-10-13-atm", '["Yes", "No"]', "Will Club Atlético de Madrid win on 2026-10-13?", "Club Atlético de Madrid")]),
                    _event("epl-tot-cov-2026-10-19", tag, [
                        _ml("c-tot", "epl-tot-cov-2026-10-19-tot", '["Yes", "No"]', "Will Tottenham win?", "Tottenham Hotspur FC")])]
        return []

    monkeypatch.setattr(PolymarketClient, "_get", fake_get)
    monkeypatch.setattr("sportsbot.exchanges.polymarket.SPORT_TAGS", {**SPORT_TAGS, "soccer": [100977, 306]})
    ms = PolymarketClient().list_sports_markets("soccer")
    assert [m.market_id for m in ms] == ["c-atm", "c-draw", "c-mnu", "c-tot"]     # de-duplicated across tags
    assert {t["tag_id"] for t in calls} == {100977, 306}
    atm, draw = ms[0], ms[1]
    assert atm.sport is Sport.SOCCER and atm.home == "Club Atlético de Madrid" and atm.away == "not Club Atlético de Madrid"
    assert atm.meta["three_way"] is True and atm.meta["outcome_title"] == "Club Atlético de Madrid"
    assert draw.home.startswith("Draw (") and draw.yes_token_id == "t1" and draw.no_token_id == "t2"
    # the start is read from gameStartTime (the fixture's: six hours from now)
    assert atm.start_time is not None
    assert abs(atm.start_time - (datetime.now(timezone.utc) + timedelta(hours=6))) < timedelta(minutes=1)


def test_two_sided_esports_market_keeps_team_names(monkeypatch):
    def fake_get(self, url, params=None):
        if params.get("offset", 0) > 0 or params["tag_id"] != 65:
            return []
        m = _ml("c-lol", "lol-we-fly-2026-10-13", '["Team WE", "FlyQuest"]', "LoL: Team WE vs FlyQuest (BO5)", "Match Winner")
        m["negRisk"] = False
        return [_event("lol-we-fly-2026-10-13", 65, [m, {**m, "conditionId": "c-map", "sportsMarketType": "child_moneyline"}])]

    monkeypatch.setattr(PolymarketClient, "_get", fake_get)
    monkeypatch.setattr("sportsbot.exchanges.polymarket.SPORT_TAGS", {**SPORT_TAGS, "esports": [65]})
    ms = PolymarketClient().list_sports_markets("esports")
    assert len(ms) == 1 and ms[0].sport is Sport.ESPORTS       # map markets are not moneylines
    assert (ms[0].home, ms[0].away) == ("Team WE", "FlyQuest") and "three_way" not in ms[0].meta
