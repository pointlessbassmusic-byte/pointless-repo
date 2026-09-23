"""Sportsbook-vs-Polymarket study: parsing both sources, conservative team
matching, and the scoring arithmetic (Brier, regression, strategy)."""
import random
from datetime import datetime, timezone

from src.sportsbook_study import (
    BookRow, PolyMatch, brier, match, ols, paired_brier_diff, parse_book_csv,
    parse_event, strategy, team,
)

CSV = (
    "﻿Div,Date,Time,HomeTeam,AwayTeam,FTR,AvgH,AvgD,AvgA,AvgCH,AvgCD,AvgCA,"
    "B365CH,B365CD,B365CA,PSCH,PSCD,PSCA,BFECH,BFECD,BFECA\n"
    "E0,15/08/2025,20:00,Liverpool,Bournemouth,H,1.30,6.00,8.50,1.32,5.75,8.00,"
    "1.30,6.00,9.00,,,,1.33,6.2,9.4\n"
    "E0,16/08/2025,12:30,Aston Villa,Newcastle,D,2.25,3.50,2.90,2.30,3.60,2.87,"
    "2.30,3.50,3.00,2.38,3.55,2.95,2.4,3.7,3.05\n"
    "E0,,,,,,,,,,,,,,,,,,,,\n"
)


def test_parse_book_csv_devigs_and_tolerates_blank_books():
    rows = parse_book_csv(CSV, "E0")
    assert [r.home for r in rows] == ["Liverpool", "Aston Villa"]
    r = rows[0]
    assert r.date == datetime(2025, 8, 15) and r.result == "H"
    assert abs(sum(r.avg_close.values()) - 1) < 1e-9
    assert abs(r.avg_close["H"] - (1 / 1.32) / (1 / 1.32 + 1 / 5.75 + 1 / 8.00)) < 1e-9
    assert r.pin_close == {}                       # blank Pinnacle columns -> no reference
    assert rows[1].pin_close and abs(sum(rows[1].pin_close.values()) - 1) < 1e-9


def test_team_aliases_cover_both_polymarket_naming_conventions():
    assert team("Manchester City FC") == team("Manchester City") == "Man City"
    assert team("Wolverhampton Wanderers FC") == team("Wolves") == "Wolves"
    assert team("Club Atlético de Madrid") == team("Atletico Madrid") == "Ath Madrid"
    assert team("Borussia Mönchengladbach") == "M'gladbach"
    assert team("Paris Saint-Germain FC") == "Paris SG"
    assert team("Some Random Club") is None      # unmatched is skipped, never guessed


def _event(home="Arsenal FC", away="Chelsea FC", winner="H", start="2026-03-01 16:30:00+00"):
    def mk(q, yes, cond):
        return {"question": q, "sportsMarketType": "moneyline", "gameStartTime": start,
                "outcomePrices": '["1", "0"]' if yes else '["0", "1"]',
                "clobTokenIds": f'["{cond}-y", "{cond}-n"]', "conditionId": cond,
                "volumeNum": "1000"}
    return {"slug": "epl-ars-che-2026-03-01", "title": f"{home} vs. {away}", "markets": [
        mk(f"Will {home} win on 2026-03-01?", winner == "H", "c1"),
        mk(f"Will {home} vs. {away} end in a draw?", winner == "D", "c2"),
        mk(f"Will {away} win on 2026-03-01?", winner == "A", "c3"),
    ]}


def test_parse_event_maps_the_three_markets_and_reads_the_winner():
    pm = parse_event(_event(winner="A"), "E0")
    assert pm.home == "Arsenal FC" and pm.away == "Chelsea FC" and pm.result == "A"
    assert pm.kickoff == datetime(2026, 3, 1, 16, 30, tzinfo=timezone.utc)
    assert pm.markets["H"]["condition_id"] == "c1" and pm.markets["A"]["yes_token"] == "c3-y"
    # a voided match resolves every market NO: no winner, skipped
    ev = _event()
    for m in ev["markets"]:
        m["outcomePrices"] = '["0", "1"]'
    assert parse_event(ev, "E0") is None
    # an unresolved market is skipped too
    ev = _event()
    ev["markets"][0]["outcomePrices"] = '["0.55", "0.45"]'
    assert parse_event(ev, "E0") is None


def _book(home, away, date, result="H", league="E0"):
    p = {"H": 0.5, "D": 0.25, "A": 0.25}
    return BookRow(league=league, date=date, home=home, away=away, result=result,
                   avg_open=p, avg_close=p, b365_close=p, pin_close=p, bfe_close=p)


def test_match_requires_league_teams_date_window_and_agreeing_result():
    pm = parse_event(_event(), "E0")
    good = _book("Arsenal", "Chelsea", datetime(2026, 3, 1))
    pairs, unmatched = match([pm], [good])
    assert len(pairs) == 1 and pairs[0][1] is good and unmatched == set()
    # a day later in local time still pairs (late kickoffs cross midnight UTC)
    assert len(match([pm], [_book("Arsenal", "Chelsea", datetime(2026, 3, 2))])[0]) == 1
    # the reverse fixture months earlier does not
    assert match([pm], [_book("Chelsea", "Arsenal", datetime(2025, 11, 1))])[0] == []
    # same fixture in another league table does not
    assert match([pm], [_book("Arsenal", "Chelsea", datetime(2026, 3, 1), league="SP1")])[0] == []
    # a result the two sources disagree on is skipped, not trusted either way
    assert match([pm], [_book("Arsenal", "Chelsea", datetime(2026, 3, 1), result="A")])[0] == []
    # unknown names are reported, not guessed
    odd = parse_event(_event(home="Arsenal FC", away="Nowhere Rovers"), "E0")
    assert odd is not None
    assert match([odd], [good])[1] == {"Nowhere Rovers"}


def _rows(n=300, seed=1):
    """Synthetic matches where the book is calibrated and Polymarket is a
    noisy copy of the book: the book should score better and carry the signal."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        p = rng.uniform(0.15, 0.7)
        y = 1 if rng.random() < p else 0
        rows.append({"slug": f"m{i}", "outcome": y, "avg_close": p,
                     "pm_price": min(0.95, max(0.05, p + rng.gauss(0, 0.08)))})
    return rows


def test_brier_and_paired_bootstrap_prefer_the_calibrated_source():
    rows = _rows()
    assert brier(rows, "avg_close") < brier(rows, "pm_price")
    d, lo, hi = paired_brier_diff(rows, "pm_price", "avg_close", n_boot=300)
    assert d > 0 and lo > 0            # polymarket worse, CI excludes zero


def test_ols_recovers_a_planted_relationship():
    rng = random.Random(3)
    rows = [{"a": rng.random(), "b": rng.random()} for _ in range(2000)]
    for r in rows:
        r["outcome"] = 0.2 + 0.5 * r["a"] - 0.3 * r["b"] + rng.gauss(0, 0.01)
    fit = ols(rows, ("a", "b"))
    assert abs(fit["const"][0] - 0.2) < 0.01
    assert abs(fit["a"][0] - 0.5) < 0.01 and fit["a"][1] > 20
    assert abs(fit["b"][0] + 0.3) < 0.01 and fit["b"][1] < -20


def test_strategy_pays_the_spread_and_only_buys_above_threshold():
    rows = [
        {"pm_price": 0.40, "avg_close": 0.45, "outcome": 1},   # gap 0.05: bought, wins
        {"pm_price": 0.40, "avg_close": 0.45, "outcome": 0},   # gap 0.05: bought, loses
        {"pm_price": 0.40, "avg_close": 0.41, "outcome": 1},   # gap 0.01: not bought
    ]
    s = strategy(rows, "avg_close", 0.03, spread=0.01)
    assert s["n"] == 2 and s["hit"] == 0.5
    assert abs(s["mean_ret"] - ((1 / 0.41 - 1) + (-1)) / 2) < 1e-9
    assert strategy(rows, "avg_close", 0.10)["n"] == 0


def test_polymatch_dataclass_carries_what_sampling_needs():
    pm = PolyMatch(league="E0", slug="s", home="A", away="B",
                   kickoff=datetime(2026, 1, 1, tzinfo=timezone.utc), markets={}, result="H")
    assert pm.kickoff.tzinfo is timezone.utc
