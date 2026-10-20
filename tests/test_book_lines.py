"""Forward record of the live sportsbook line vs Polymarket US / main."""
from datetime import datetime, timezone

import pytest

from sportsbot.core.types import Exchange, MarketInfo, MarketQuote
from sportsbot.signals import book_lines as bl

START = datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc).timestamp()


def _odds(home_ml, away_ml):
    return {"items": [{"provider": {"name": "DraftKings"},
                       "homeTeamOdds": {"moneyLine": home_ml, "current": {"moneyLine": {"american": str(home_ml)}}},
                       "awayTeamOdds": {"moneyLine": away_ml, "current": {"moneyLine": {"american": str(away_ml)}}}}]}


def test_current_line_reads_the_live_shape_and_devigs():
    ln = bl.current_line(_odds(-150, 130))
    p_h, p_a = 150 / 250, 100 / 230
    assert ln["provider"] == "DraftKings" and ln["book_home"] == pytest.approx(p_h / (p_h + p_a))
    assert bl.current_line({"items": [{"homeTeamOdds": {}, "awayTeamOdds": {}}]}) is None


def test_side_price_buys_from_a_yes_frame_book():
    assert bl.side_price(True, 0.40, 0.42, home=True) == 0.42        # home is YES: its ask
    assert bl.side_price(True, 0.40, 0.42, home=False) == 0.60       # away = NO: 1 - bid
    assert bl.side_price(False, 0.40, 0.42, home=True) == 0.60       # home is NO
    assert bl.side_price(True, None, 0.42, home=False) is None


class Http:
    def __init__(self, clock, home_ml=-150, away_ml=130):
        self.clock, self.home_ml, self.away_ml, self.final = clock, home_ml, away_ml, False

    def get(self, url, params=None):
        class R:
            def __init__(s, j):
                s._j = j

            def raise_for_status(s):
                pass

            def json(s):
                return s._j
        if url.endswith("/scoreboard"):
            team = lambda n, loc: {"name": n, "displayName": f"{loc} {n}", "location": loc}  # noqa: E731
            return R({"events": [{"id": "g1", "date": "2026-10-21T00:00Z",
                                  "status": {"type": {"completed": self.final}},
                                  "competitions": [{"competitors": [
                                      {"homeAway": "home", "team": team("Pistons", "Detroit"), "winner": self.final},
                                      {"homeAway": "away", "team": team("Celtics", "Boston"), "winner": False}]}]}]})
        return R(_odds(self.home_ml, self.away_ml))


class Venue:
    def __init__(self, home, away, bid, ask):
        self.m = MarketInfo(exchange=Exchange.POLYMARKET_US, market_id=f"{home}-{away}", home=home, away=away,
                            start_time=datetime.fromtimestamp(START, timezone.utc))
        self.bid, self.ask = bid, ask

    def list_sports_markets(self, sport):
        return [self.m]

    def get_quote(self, m):
        return MarketQuote(market_id=m.market_id, bid=self.bid, ask=self.ask)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_records_checkpoints_once_matches_both_venues_and_settles(tmp_path):
    clock = Clock(START - 15 * 60)
    http = Http(clock)
    # US lists the away team first (YES = Celtics); main lists the home team first
    us = Venue("Boston Celtics", "Detroit Pistons", bid=0.47, ask=0.48)
    pm = Venue("Detroit Pistons", "Boston Celtics", bid=0.52, ask=0.53)
    rec = bl.Recorder(str(tmp_path / "b.sqlite"), us=us, pm=pm, http=http, clock=clock, sports=("nba",))
    assert rec.cycle()["snapshots"] == 1
    assert rec.cycle()["snapshots"] == 0                          # a checkpoint is taken once
    row = rec.conn.execute("SELECT checkpoint, us_home_is_yes, us_bid, pm_home_is_yes, pm_ask, provider "
                           "FROM snaps").fetchone()
    assert row == (15, 0, 0.47, 1, 0.53, "DraftKings")
    http.final = True
    rec._boards.clear()
    clock.t = START + 4 * 3600
    assert rec.cycle()["settled"] == 1
    # book says Pistons (home) ~0.58; US price to buy Pistons = 1 - 0.47 = 0.53 -> a trade; Pistons won
    (t,) = bl.trades(rec.conn, "us")
    assert t["price"] == pytest.approx(0.53)
    assert t["net"] == pytest.approx(1 - 0.53 - bl.US_TAKER * 0.53 * 0.47)
    (t2,) = bl.trades(rec.conn, "pm")
    assert t2["net"] == pytest.approx(1 - 0.53 - 0.03 * 0.53 * 0.47)       # NBA main fee 0.03


def test_no_trade_when_the_gap_is_under_the_margin(tmp_path):
    clock = Clock(START - 15 * 60)
    http = Http(clock, home_ml=-110, away_ml=-110)                 # 50/50
    us = Venue("Detroit Pistons", "Boston Celtics", bid=0.49, ask=0.51)
    rec = bl.Recorder(str(tmp_path / "b.sqlite"), us=us, pm=us, http=http, clock=clock, sports=("nba",))
    rec.cycle()
    http.final = True
    rec._boards.clear()
    clock.t = START + 4 * 3600
    rec.cycle()
    assert bl.trades(rec.conn, "us") == []


def test_the_bar_is_the_users_one_sided_test():
    assert bl.verdict({"n": 99, "mean": 0.05, "t": 3.0}).startswith("collecting")
    assert bl.verdict({"n": 100, "mean": 0.02, "t": 1.7}).startswith("PASSES")
    assert bl.verdict({"n": 100, "mean": 0.02, "t": 1.6}).startswith("no edge")
    assert bl.verdict({"n": 120, "mean": -0.01, "t": -1.0}).startswith("killed")
