"""Queue study: game matching, sampling both venues, strict-fill counting."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from sportsbot.core.types import BookLevel, Exchange, MarketInfo, MarketQuote, Sport, TapePrint
from sportsbot.substrate_bridge import queue_study as qs

T0 = 1_800_000_000.0
START = datetime.fromtimestamp(T0 + 3600, timezone.utc)


def _mi(exchange, mid, home, away, sport=Sport.HOCKEY, start=START, close=None):
    return MarketInfo(exchange=exchange, market_id=mid, yes_token_id=f"{mid}-y", no_token_id=f"{mid}-n",
                      question="", slug=mid, sport=sport, home=home, away=away, start_time=start,
                      close_time=close, active=True, tick_size=0.01, min_order_size=1, neg_risk=False)


def _q(mid, bid, ask, bq, aq):
    return MarketQuote(market_id=mid, bid=bid, ask=ask, bids=[BookLevel(price=bid, size=bq)],
                       asks=[BookLevel(price=ask, size=aq)])


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 1.0)


class FakeUS:
    def __init__(self, markets):
        self.markets = markets
        self.bbo = {"traded": 100.0, "last_px": 0.49}

    def list_sports_markets(self, sport):
        return [m for m in self.markets if m.sport.value == sport]

    def get_bbo(self, slug):
        return dict(self.bbo)

    def get_quote(self, m):
        return _q(m.market_id, 0.49, 0.50, 60000.0, 120000.0)


class FakePM:
    quote = (0.48, 0.51, 900.0, 300.0)                         # main YES = the US short side

    def __init__(self, markets, prints=()):
        self.markets = markets
        self.prints = list(prints)
        self.tape_calls = []

    def list_sports_markets(self, sport):
        return [m for m in self.markets if m.sport.value == sport]

    def get_quote(self, m):
        return _q(m.market_id, *self.quote)

    def recent_trades(self, m, since):
        self.tape_calls.append(since)
        return [p for p in self.prints if p.ts >= since]


class FakeKalshi(FakePM):
    quote = (0.47, 0.50, 40.0, 25.0)


def test_main_book_matches_on_names_and_start_not_on_names_alone():
    us = _mi(Exchange.POLYMARKET_US, "aec-nhl-nj-det", "New Jersey Devils", "Detroit Red Wings")
    same_flipped = _mi(Exchange.POLYMARKET, "c1", "Detroit Red Wings", "New Jersey Devils")
    next_day = _mi(Exchange.POLYMARKET, "c2", "New Jersey Devils", "Detroit Red Wings",
                   start=START + timedelta(days=1))
    other = _mi(Exchange.POLYMARKET, "c3", "Boston Bruins", "Detroit Red Wings")
    pm, same = qs.match_main("hockey", us, [next_day, other, same_flipped])
    assert pm.market_id == "c1" and same is False
    assert qs.match_main("hockey", us, [next_day, other]) is None      # a series rematch is another game
    us_mlb = _mi(Exchange.POLYMARKET_US, "aec-mlb-cle-tb", "Cleveland Guardians", "Tampa Bay Rays",
                 sport=Sport.BASEBALL)
    pm_mlb = _mi(Exchange.POLYMARKET, "c4", "Cleveland Guardians", "Tampa Bay Rays", sport=Sport.BASEBALL)
    assert qs.match_main("baseball", us_mlb, [pm_mlb])[1] is True


def test_kalshi_matches_on_names_and_a_close_shortly_after_the_us_start():
    us = _mi(Exchange.POLYMARKET_US, "aec-nhl-nj-det", "New Jersey Devils", "Detroit Red Wings")
    tonight = _mi(Exchange.KALSHI, "KXNHLGAME-26OCT13NJDET-DET", "Detroit Red Wings", "New Jersey Devils",
                  start=None, close=START + timedelta(hours=3))
    next_week = _mi(Exchange.KALSHI, "KXNHLGAME-26OCT20NJDET-DET", "Detroit Red Wings", "New Jersey Devils",
                    start=None, close=START + timedelta(days=7, hours=3))
    no_times = _mi(Exchange.KALSHI, "KX-X", "Detroit Red Wings", "New Jersey Devils", start=None)
    k, same = qs.match_kalshi("hockey", us, [next_week, no_times, tonight])
    assert k.market_id == "KXNHLGAME-26OCT13NJDET-DET" and same is False
    assert qs.match_kalshi("hockey", us, [next_week, no_times]) is None
    mlb_us = _mi(Exchange.POLYMARKET_US, "aec-mlb", "Cleveland Guardians", "Tampa Bay Rays", sport=Sport.BASEBALL)
    mlb_k = _mi(Exchange.KALSHI, "KXMLBGAME-X-CLE", "cleveland guardians", "tampa bay rays",
                sport=Sport.BASEBALL, start=START + timedelta(minutes=5))
    assert qs.match_kalshi("baseball", mlb_us, [mlb_k])[1] is True    # Kalshi's own start, when it has one


def test_kalshi_tennis_close_at_or_before_the_us_start_is_the_same_match():
    """Kalshi's tennis close is the SCHEDULED time: on Oct 25 it sat at or
    before the US start for 5 of 39 matches, which a close-after-start rule
    dropped. Yesterday's meeting of the same names is still another match."""
    us = _mi(Exchange.POLYMARKET_US, "aec-atp-jas-kik", "Omar Jasika", "Yuta Kikuchi", sport=Sport.TENNIS)
    sched = _mi(Exchange.KALSHI, "KXATPCHALLENGERMATCH-26OCT26JASKIK-JAS", "Omar Jasika", "Yuta Kikuchi",
                sport=Sport.TENNIS, start=None, close=START - timedelta(minutes=30))
    assert qs.match_kalshi("tennis", us, [sched])[0].market_id == sched.market_id
    yesterday = _mi(Exchange.KALSHI, "KXATPCHALLENGERMATCH-26OCT25JASKIK-JAS", "Omar Jasika", "Yuta Kikuchi",
                    sport=Sport.TENNIS, start=None, close=START - timedelta(hours=21))
    assert qs.match_kalshi("tennis", us, [yesterday]) is None


def test_sampler_stores_both_venues_in_the_us_long_frame_and_stops_at_the_start(tmp_path):
    clock = Clock()
    us_m = _mi(Exchange.POLYMARKET_US, "aec-nhl-nj-det", "New Jersey Devils", "Detroit Red Wings")
    late = _mi(Exchange.POLYMARKET_US, "aec-nhl-late", "Boston Bruins", "Ottawa Senators",
               start=START + timedelta(hours=20))
    pm_m = _mi(Exchange.POLYMARKET, "c1", "Detroit Red Wings", "New Jersey Devils")
    prints = [TapePrint(ts=datetime.fromtimestamp(T0 + 10, timezone.utc), price_yes=0.52, size=5.0),
              TapePrint(ts=datetime.fromtimestamp(T0 - 600, timezone.utc), price_yes=0.40, size=9.0)]
    k_m = _mi(Exchange.KALSHI, "KXNHLGAME-NJDET-NJ", "New Jersey Devils", "Detroit Red Wings",
              start=None, close=START + timedelta(hours=3))
    pm = FakePM([pm_m], prints)
    kalshi = FakeKalshi([k_m], [TapePrint(ts=datetime.fromtimestamp(T0 + 15, timezone.utc),
                                          price_yes=0.46, size=7.0)])
    s = qs.QueueSampler(str(tmp_path / "q.sqlite"), us=FakeUS([us_m, late]), pm=pm, kalshi=kalshi,
                        clock=clock)
    assert s.discover(("hockey",), window_h=12) == 1                   # the 21 h game is outside
    assert s.sample_once() == 3
    conn = sqlite3.connect(str(tmp_path / "q.sqlite"))
    rows = {r[0]: r[1:] for r in conn.execute(
        "SELECT venue, bid, ask, bid_qty, ask_qty, traded, last_px FROM samples")}
    assert rows["us"] == (0.49, 0.50, 60000.0, 120000.0, 100.0, 0.49)
    # main YES (Detroit) bid 0.48 / ask 0.51 is a Devils bid 0.49 / ask 0.52, sizes swapped
    assert rows["pm"] == (pytest.approx(0.49), pytest.approx(0.52), 300.0, 900.0, None, None)
    assert rows["kalshi"] == (0.47, 0.50, 40.0, 25.0, None, None)      # Kalshi YES = Devils: as is
    assert conn.execute("SELECT k_ticker, k_yes_is_us_long, pm_yes_is_us_long FROM games").fetchone() == (
        "KXNHLGAME-NJDET-NJ", 1, 0)
    clock.t = T0 + 20
    assert s.store_tape(T0) == 2                                        # only prints after the run began
    assert conn.execute("SELECT venue, price, size FROM prints ORDER BY venue").fetchall() == [
        ("kalshi", 0.46, 7.0), ("pm", pytest.approx(0.48), 5.0)]
    clock.t = START.timestamp()
    assert s.sample_once() == 0                                         # started: pre-game only
    s.conn.close()


def test_run_loops_until_the_deadline_and_collects_the_tape_at_the_end(tmp_path):
    clock = Clock()
    us_m = _mi(Exchange.POLYMARKET_US, "aec-nhl-nj-det", "New Jersey Devils", "Detroit Red Wings")
    pm_m = _mi(Exchange.POLYMARKET, "c1", "New Jersey Devils", "Detroit Red Wings")
    s = qs.QueueSampler(str(tmp_path / "q.sqlite"), us=FakeUS([us_m]), pm=FakePM([pm_m]),
                        kalshi=FakeKalshi([]), clock=clock)
    res = s.run(minutes=5, interval=30, sports=("hockey",), sleep=clock.sleep)
    assert res["games"] == 1 and res["passes"] == 10 and res["rows"] == 20


def _seed(path, us_rows, pm_rows=(), prints=(), start=T0 + 600):
    conn = sqlite3.connect(path)
    conn.executescript(qs.SCHEMA)
    conn.execute("INSERT INTO games(game_id,sport,us_slug,us_long,us_short,start_ts,pm_condition,"
                 "pm_yes_token,pm_no_token,pm_yes_is_us_long,created_ts) "
                 "VALUES (1,'hockey','g','A','B',?,'c','y','n',1,?)", (start, T0))
    conn.executemany("INSERT INTO samples VALUES (?,1,'us',?,?,?,?,?,?)", us_rows)
    conn.executemany("INSERT INTO samples VALUES (?,1,'pm',?,?,?,?,NULL,NULL)", pm_rows)
    conn.executemany("INSERT INTO prints(game_id, ts, price, size) VALUES (1,?,?,?)", prints)
    conn.commit()
    conn.close()


def test_report_counts_strict_fills_and_grades_them_against_the_closing_mid(tmp_path):
    db = str(tmp_path / "q.sqlite")
    us = [(T0 + 0, 0.49, 0.50, 6e4, 6e4, 100.0, 0.49),
          (T0 + 30, 0.49, 0.50, 6e4, 6e4, 100.0, 0.49),    # nothing traded
          (T0 + 60, 0.47, 0.48, 6e4, 6e4, 400.0, 0.48),    # traded, last 0.48 < bid 0.49: bid swept
          (T0 + 90, 0.46, 0.48, 6e4, 6e4, 450.0, 0.48),    # traded above the bid: no sweep; bid pulled
          (T0 + 590, 0.45, 0.46, 6e4, 6e4, 450.0, 0.48)]   # gap > 3 intervals: skipped; close mid 0.455
    pm = [(T0 + 0, 0.49, 0.50, 500.0, 700.0), (T0 + 30, 0.49, 0.50, 500.0, 700.0),
          (T0 + 590, 0.45, 0.46, 500.0, 700.0)]
    prints = [(T0 + 10, 0.48, 3.0), (T0 + 20, 0.50, 2.0)]   # through the bid, then back at the ask
    _seed(db, us, pm, prints)
    r = qs.report(db, interval=30)
    u = r["us"]["hockey"]
    assert u["through"] == 1 and u["clv_n"] == 1
    assert u["clv_mean"] == pytest.approx(0.455 - 0.49)     # bought at 0.49, closed at 0.455
    assert u["volume_per_hour"] == pytest.approx(350.0 / (90 / 3600))
    assert u["touch_queue_median"] == 6e4
    assert u["pulled"] == 1      # 60 -> 90: bid 0.47 -> 0.46 with nothing through it (30 -> 60 was a sweep)
    # at-or-through (the paper rule): the sweep, plus 60 -> 90's print AT the 0.48 ask
    assert u["touched"] == 2 and u["clv_at_n"] == 2
    assert u["clv_at_mean"] == pytest.approx(((0.455 - 0.49) + (0.48 - 0.455)) / 2)
    assert u["queue_turnover_per_hour"] == pytest.approx(u["volume_per_hour"] / 6e4)
    assert r["pm_coarse"]["hockey"]["through"] == 0          # last print 0.50 is not through anything
    assert r["pm_coarse"]["hockey"]["touched"] == 1          # ...but it is AT the 0.50 ask
    assert r["pm_exact"]["hockey"]["through"] == 1           # the 0.48 print is
    assert r["pm_exact"]["hockey"]["clv_mean"] == pytest.approx(0.455 - 0.49)
    assert "pm_exact" in qs.format_report(r)


def test_report_needs_a_sample_near_the_start_to_grade_clv(tmp_path):
    db = str(tmp_path / "q.sqlite")
    us = [(T0, 0.49, 0.50, 1.0, 1.0, 0.0, None), (T0 + 30, 0.49, 0.50, 1.0, 1.0, 5.0, 0.52)]
    _seed(db, us, start=T0 + 7200)                           # last sample 2 h before the start
    u = qs.report(db, interval=30)["us"]["hockey"]
    assert u["through"] == 1 and u["clv_n"] == 0 and "clv_mean" not in u


def test_a_log_written_before_kalshi_joined_migrates_and_still_reports(tmp_path):
    db = str(tmp_path / "old.sqlite")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE games (game_id INTEGER PRIMARY KEY, sport TEXT NOT NULL, us_slug TEXT NOT NULL UNIQUE,
            us_long TEXT NOT NULL, us_short TEXT NOT NULL, start_ts REAL NOT NULL, pm_condition TEXT,
            pm_yes_token TEXT, pm_no_token TEXT, pm_yes_is_us_long INTEGER, created_ts REAL NOT NULL);
        CREATE TABLE samples (ts REAL NOT NULL, game_id INTEGER NOT NULL, venue TEXT NOT NULL,
            bid REAL, ask REAL, bid_qty REAL, ask_qty REAL, traded REAL, last_px REAL);
        CREATE TABLE prints (game_id INTEGER NOT NULL, ts REAL NOT NULL, price REAL NOT NULL,
            size REAL NOT NULL);""")
    conn.execute("INSERT INTO games VALUES (1,'hockey','g','A','B',?,'c','y','n',1,?)", (T0 + 600, T0))
    conn.executemany("INSERT INTO samples VALUES (?,1,'pm',0.49,0.50,5,5,NULL,NULL)", [(T0,), (T0 + 30,)])
    conn.execute("INSERT INTO prints VALUES (1,?,0.48,2)", (T0 + 10,))
    conn.commit()
    conn.close()
    assert qs.report(db, interval=30)["pm_exact"]["hockey"]["through"] == 1   # old prints read as main's
    us = _mi(Exchange.POLYMARKET_US, "g", "New Jersey Devils", "Detroit Red Wings")
    k_m = _mi(Exchange.KALSHI, "KX-NJ", "New Jersey Devils", "Detroit Red Wings",
              start=None, close=START + timedelta(hours=3))
    s = qs.QueueSampler(db, us=FakeUS([us]), pm=FakePM([]), kalshi=FakeKalshi([k_m]), clock=Clock())
    s.discover(("hockey",))
    assert s.conn.execute("SELECT k_ticker FROM games WHERE us_slug='g'").fetchone() == ("KX-NJ",)
