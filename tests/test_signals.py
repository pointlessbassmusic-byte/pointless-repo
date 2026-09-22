"""External signals: NWS probability math, chatter flag counting, and the
exporter's forecast no-backfill rule."""

import sqlite3
import time

import pytest

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


def test_retro_query_mapping():
    from sportsbot.signals.chatter import retro_query_for_event
    assert retro_query_for_event("mlb-lad-atl-2026-08-27") == "Dodgers Braves"
    assert retro_query_for_event("mlb-xxx-atl-2026-08-27") is None   # unknown code -> skip
    assert retro_query_for_event("will-novak-djokovic-win-the-2026-australian-open") is None


def test_failed_series_is_reswept_not_dropped(monkeypatch, tmp_path):
    """A 429'd series gets a second sweep: its markets' first sighting is the
    protocol's decision point and the no-backfill rule can never rebuild it."""
    from sportsbot.substrate_bridge.kalshi_weather import WeatherSnapshotService

    svc = WeatherSnapshotService.__new__(WeatherSnapshotService)
    svc.conn = sqlite3.connect(":memory:")
    svc.conn.row_factory = sqlite3.Row
    from sportsbot.substrate_bridge.kalshi_weather import SCHEMA
    svc.conn.executescript(SCHEMA)
    svc.series = ["KXHIGHNY", "KXHIGHMIA"]
    svc.SERIES_PAUSE = svc.RETRY_SWEEP_PAUSE = 0.0
    monkeypatch.setattr(svc, "_record_forecasts", lambda now: None)
    monkeypatch.setattr(svc, "_resolve_outcomes", lambda: 0)

    calls = []

    class FakeClient:
        def _request(self, method, path, params=None, **kw):
            series = params["series_ticker"]
            calls.append(series)
            if series == "KXHIGHMIA" and calls.count("KXHIGHMIA") == 1:
                raise RuntimeError("rate limited")
            return {"markets": [{"ticker": f"{series}-T", "title": "t",
                                 "yes_bid_dollars": "0.40",
                                 "yes_ask_dollars": "0.44"}]}

        def _dollars(self, m, key):
            return float(m[key])

        def _ts(self, m, *keys):
            return None

    svc.client = FakeClient()
    out = svc.snapshot_once()
    assert calls == ["KXHIGHNY", "KXHIGHMIA", "KXHIGHMIA"]   # one re-sweep
    assert out["recorded"] == 2 and out["missing_series"] == []
    tickers = {r[0] for r in svc.conn.execute("SELECT ticker FROM weather_snapshots")}
    assert tickers == {"KXHIGHNY-T", "KXHIGHMIA-T"}


def test_series_down_all_pass_is_reported_not_silent(monkeypatch):
    """Still failing after the re-sweep → named in missing_series, so the loop
    logs which station lost its decision-time row instead of dropping it."""
    from sportsbot.substrate_bridge.kalshi_weather import SCHEMA, WeatherSnapshotService

    svc = WeatherSnapshotService.__new__(WeatherSnapshotService)
    svc.conn = sqlite3.connect(":memory:")
    svc.conn.row_factory = sqlite3.Row
    svc.conn.executescript(SCHEMA)
    svc.series = ["KXHIGHMIA"]
    svc.SERIES_PAUSE = svc.RETRY_SWEEP_PAUSE = 0.0
    monkeypatch.setattr(svc, "_record_forecasts", lambda now: None)
    monkeypatch.setattr(svc, "_resolve_outcomes", lambda: 0)

    class DeadClient:
        def _request(self, *a, **kw):
            raise RuntimeError("rate limited")

    svc.client = DeadClient()
    assert svc.snapshot_once()["missing_series"] == ["KXHIGHMIA"]


def test_kalshi_order_placement_never_auto_retries(monkeypatch):
    """place_order must not resubmit: the same client_order_id sent twice
    either duplicates the order or is rejected while the first is live
    (ExchangeClient.place_order promises no blind resubmits)."""
    import httpx

    from sportsbot.core.types import Order, OrderType, Side
    from sportsbot.exchanges.kalshi import KalshiClient

    c = KalshiClient(env="demo")
    monkeypatch.setattr(c, "_auth_headers", lambda *a, **kw: {})
    attempts = {"post": 0, "get": 0}

    def fake_request(method, url, params=None, json=None, headers=None):
        attempts["post" if method == "POST" else "get"] += 1
        req = httpx.Request(method, url)
        return httpx.Response(429, request=req)

    monkeypatch.setattr(c.http, "request", fake_request)

    order = Order(market_id="KXHIGHNY-X", side=Side.YES, size=10.0,
                  price=0.40, order_type=OrderType.LIMIT)
    c.place_order(order)
    assert attempts["post"] == 1          # submitted once, never resubmitted

    # reads still retry — a dropped snapshot loses a decision point for good
    import tenacity
    monkeypatch.setattr(KalshiClient._request.retry, "wait", tenacity.wait_none())
    try:
        c._request("GET", "/markets")
    except httpx.HTTPStatusError:
        pass
    assert attempts["get"] == 5


def test_score_arms_keeps_arms_separate_and_honors_no_backfill(tmp_path, monkeypatch):
    """Each arm is scored at the same decision point, and a forecast recorded
    after the first sighting never reaches the NWS arm."""
    import sportsbot.substrate_bridge.climatology as climo_mod
    from sportsbot.substrate_bridge.kalshi_weather import SCHEMA, score_arms

    db = tmp_path / "w.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    first_ts = time.time() - 86400
    title = "Will the maximum temperature be >70° on Sep 16, 2026?"
    rows = [("A", 0.80, 1), ("B", 0.20, 0)]
    for ticker, mid, outcome in rows:
        conn.execute("INSERT INTO weather_snapshots (ts, ticker, series, title,"
                     " yes_bid, yes_ask, close_ts) VALUES (?,?,?,?,?,?,?)",
                     (first_ts, ticker, "KXHIGHNY", title, mid - 0.01, mid + 0.01, first_ts))
        conn.execute("INSERT INTO weather_outcomes (ticker, outcome, settled_ts)"
                     " VALUES (?,?,?)", (ticker, outcome, first_ts + 3600))
    # forecast recorded AFTER the decision point -> must not be used
    conn.execute("INSERT INTO weather_forecasts (ts, series, target_date, forecast_high)"
                 " VALUES (?,?,?,?)", (first_ts + 600, "KXHIGHNY", "2026-09-16", 95))
    conn.commit()
    conn.close()
    monkeypatch.setattr(climo_mod.Climatology, "prob", lambda self, s, t: 0.60)

    res = score_arms(str(db))
    assert res["all"]["n"] == 2
    assert res["all"]["nws_n"] == 0 and res["all"]["nws"] is None
    assert res["nws_covered"]["n"] == 0
    assert res["all"]["coin"] == 0.25
    assert res["all"]["climatology"] == pytest.approx(0.5 * (0.16 + 0.36))
    assert res["all"]["market"] == pytest.approx(0.04)          # arms stay separate

    # same forecast recorded AT the decision point -> now it counts
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO weather_forecasts (ts, series, target_date, forecast_high)"
                 " VALUES (?,?,?,?)", (first_ts, "KXHIGHNY", "2026-09-16", 95))
    conn.commit()
    conn.close()
    res = score_arms(str(db))
    assert res["nws_covered"]["n"] == 2 and res["all"]["nws"] is not None
