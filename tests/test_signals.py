"""External signals: NWS probability math, chatter flag counting, and the
exporter's forecast no-backfill rule."""

import sqlite3
import time

from sportsbot.signals.chatter import count_flags, entities_from_events_csv
from sportsbot.signals.nws import prob_from_high

T = "Will the maximum temperature be {}° on Sep 16, 2026?"


def test_nws_prob_tails_and_monotonicity():
    # forecast 83 → ">82" (TMAX>=83) is a near coin-or-better, ">90" tiny
    hot = prob_from_high(T.format(">82"), 83.0)
    scorch = prob_from_high(T.format(">90"), 83.0)
    assert hot is not None and scorch is not None and hot > 0.5 > scorch
    assert prob_from_high(T.format(">82"), 90.0) > hot  # warmer forecast → higher P


def test_nws_bins_partition_to_one():
    total = sum(prob_from_high(T.format(f"{lo}-{lo + 1}"), 83.0)
                for lo in range(63, 104, 2))
    # bins tile the whole plausible range; clipping at 0.01 inflates slightly
    assert 0.9 < total < 1.3


def test_nws_complement():
    p_over = prob_from_high(T.format(">82"), 84.0)
    p_under = prob_from_high(T.format("<83"), 84.0)   # TMAX <= 82: complement
    assert abs((p_over + p_under) - 1.0) < 0.02


def test_nws_unparseable_returns_none():
    assert prob_from_high("Will it rain?", 80.0) is None


def test_count_flags():
    flagged, per = count_flags([
        "Star pitcher scratched tonight, injury concern",
        "great game yesterday",
        "rain delay in the 3rd inning",
    ])
    assert flagged == 2
    assert per["scratched"] == 1 and per["rain delay"] == 1 and per["injury"] == 1


def test_entities_from_events_csv(tmp_path):
    p = tmp_path / "ev.csv"
    p.write_text("event_id,domain,close_time,resolve_time,market_prob,baseline_prob,outcome\n"
                 "mlb-dodgers-braves-2026,sports,1,2,0.5,0.5,1\n")
    ents = entities_from_events_csv(str(p))
    assert "dodgers" in ents and "braves" in ents and "2026" not in ents


def test_export_never_uses_post_decision_forecast(tmp_path, monkeypatch):
    """A forecast recorded AFTER the market's first snapshot must not become
    its baseline; the exporter falls back (here: unknown station -> 0.5)."""
    from sportsbot.substrate_bridge.kalshi_weather import WeatherSnapshotService

    svc = WeatherSnapshotService.__new__(WeatherSnapshotService)
    svc.conn = sqlite3.connect(":memory:")
    svc.conn.row_factory = sqlite3.Row
    from sportsbot.substrate_bridge.kalshi_weather import SCHEMA
    svc.conn.executescript(SCHEMA)

    first_ts = time.time()
    title = "Will the maximum temperature be >82° on Sep 16, 2026?"
    svc.conn.execute(
        "INSERT INTO weather_snapshots (ts, ticker, series, title, yes_bid,"
        " yes_ask, close_ts) VALUES (?,?,?,?,?,?,?)",
        (first_ts, "KXHIGHNY-26SEP16-T82", "KXHIGHNY", title, 0.40, 0.44, first_ts + 3600))
    svc.conn.execute(
        "INSERT INTO weather_outcomes (ticker, outcome, settled_ts) VALUES (?,?,?)",
        ("KXHIGHNY-26SEP16-T82", 1, first_ts + 7200))
    # forecast arrives 10 minutes AFTER the decision snapshot
    svc.conn.execute(
        "INSERT INTO weather_forecasts (ts, series, target_date, forecast_high)"
        " VALUES (?,?,?,?)", (first_ts + 600, "KXHIGHNY", "2026-09-16", 90))
    svc.conn.commit()

    # keep the test offline: climatology must not fetch
    import sportsbot.substrate_bridge.climatology as cl
    monkeypatch.setattr(cl.Climatology, "prob", lambda self, s, t: None)

    out = tmp_path / "ev.csv"
    res = svc.export_ingest_csv(str(out))
    assert res["nws_rows"] == 0
    row = out.read_text().splitlines()[1].split(",")
    assert row[5] == "0.5000"

    # now a forecast AT the decision snapshot: it must be used
    svc.conn.execute(
        "INSERT INTO weather_forecasts (ts, series, target_date, forecast_high)"
        " VALUES (?,?,?,?)", (first_ts, "KXHIGHNY", "2026-09-16", 90))
    svc.conn.commit()
    res = svc.export_ingest_csv(str(out))
    assert res["nws_rows"] == 1
    baseline = float(out.read_text().splitlines()[1].split(",")[5])
    assert baseline > 0.9  # forecast 90 vs threshold >82
