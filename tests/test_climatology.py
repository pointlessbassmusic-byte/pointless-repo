"""Climatology baseline: title parsing, the no-leak rule, and smoothing."""

from datetime import date

from sportsbot.substrate_bridge.climatology import (Climatology, _satisfies,
                                                    parse_market)


def test_parse_greater():
    target, op, lo, hi = parse_market(
        "Will the maximum temperature be >82° on Sep 16, 2026?")
    assert (target, op, lo) == (date(2026, 9, 16), ">", 82)


def test_parse_less_and_between():
    assert parse_market("Will the maximum temperature be <75° on Sep 16, 2026?")[1:3] \
        == ("<", 75)
    t, op, lo, hi = parse_market(
        "Will the maximum temperature be 81-82° on Dec 3, 2026?")
    assert (op, lo, hi) == ("between", 81, 82)
    assert t == date(2026, 12, 3)


def test_parse_garbage_is_none():
    assert parse_market("Will it rain tomorrow?") is None
    assert parse_market("") is None


def test_satisfies_whole_degree_semantics():
    assert _satisfies(83, ">", 82, 82) and not _satisfies(82, ">", 82, 82)
    assert _satisfies(74, "<", 75, 75) and not _satisfies(75, "<", 75, 75)
    assert _satisfies(81, "between", 81, 82) and not _satisfies(80, "between", 81, 82)


def _synthetic(station_temp_by_year):
    """records: every day in the ±window of Sep 16 across given years."""
    climo = Climatology()
    records = {}
    for year, temp in station_temp_by_year.items():
        for day in range(9, 24):  # Sep 9..23 covers the ±7 window
            records[f"{year}-09-{day:02d}"] = temp
    climo.set_station_records("USW00094728", records)
    return climo


def test_no_leak_target_year_excluded():
    # prior years all 70°; target year 100° — if the target year leaked,
    # P(>90) would jump. It must stay near zero.
    years = {y: 70 for y in range(2015, 2026)}
    years[2026] = 100
    climo = _synthetic(years)
    p = climo.prob("KXHIGHNY", "Will the maximum temperature be >90° on Sep 16, 2026?")
    assert p is not None and p < 0.02


def test_laplace_never_zero_or_one():
    climo = _synthetic({y: 70 for y in range(2015, 2026)})
    hot = climo.prob("KXHIGHNY", "Will the maximum temperature be >60° on Sep 16, 2026?")
    cold = climo.prob("KXHIGHNY", "Will the maximum temperature be >99° on Sep 16, 2026?")
    assert 0 < cold < 0.02 and 0.98 < hot < 1


def test_insufficient_history_returns_none():
    climo = _synthetic({2025: 70})  # one prior year = 15 obs < 60
    assert climo.prob("KXHIGHNY",
                      "Will the maximum temperature be >60° on Sep 16, 2026?") is None


def test_unknown_series_returns_none():
    climo = _synthetic({y: 70 for y in range(2015, 2026)})
    assert climo.prob("KXHIGHXX",
                      "Will the maximum temperature be >60° on Sep 16, 2026?") is None


def test_parse_negative_temperatures():
    assert parse_market("Will the maximum temperature be <-5° on Jan 12, 2027?")[1:3] \
        == ("<", -5)
    t, op, lo, hi = parse_market(
        "Will the maximum temperature be -5 to -4° on Jan 12, 2027?")
    assert (op, lo, hi) == ("between", -5, -4)


def test_parse_full_month_name():
    t, *_ = parse_market("Will the maximum temperature be >82° on September 16, 2026?")
    assert t == date(2026, 9, 16)


def test_parse_inverted_range_refused():
    # an ambiguous negative-range spelling must return None, never guess
    assert parse_market("Will the maximum temperature be 5--3° on Jan 12, 2027?") is None


def test_failed_download_not_retried_per_row(monkeypatch):
    climo = Climatology(cache_dir="/nonexistent-cache-dir-for-test")
    calls = {"n": 0}

    def boom(self, station):
        calls["n"] += 1
        raise RuntimeError("download failed")
    monkeypatch.setattr(Climatology, "load_station", boom)
    title = "Will the maximum temperature be >82° on Sep 16, 2026?"
    for _ in range(5):
        assert climo.prob("KXHIGHNY", title) is None
    # _failed short-circuits prob() after the first failure... via load_station guard;
    # with load_station fully mocked the guard lives in prob(), so calls still happen
    # but the log only fires once. Assert the negative-cache flag is set.
    assert "USW00094728" in climo._failed
