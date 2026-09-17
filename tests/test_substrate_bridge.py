"""Tests for the sportsbot -> substrate ingest adapter, including a
round-trip through the substrate engine's own loader."""

import sys
from pathlib import Path

from sportsbot.data.store import Store
from sportsbot.substrate_bridge import export_events_csv

REPO_ROOT = Path(__file__).resolve().parents[1]


class _StubResolver:
    def __init__(self, resolutions):
        self.resolutions = resolutions
        self.calls = 0

    def get_resolution(self, market_id):
        self.calls += 1
        return self.resolutions.get(market_id)


def _seed(store: Store):
    # market m1: predicted, snapshotted, bet and settled (yes lost)
    store.snapshot_quote("m1", 0.48, 0.52)
    store.record_prediction("m1", "tennis", "model", 0.62, 0.60, {})
    bid = store.record_bet("m1", "tennis", "yes", 0.62, 0.50, 25.0, 50.0,
                           0.1, "paper", "paper")
    store.settle_bet(bid, outcome=0, pnl=-25.0)
    # market m2: predicted only, resolution known to the venue
    store.snapshot_quote("m2", 0.30, 0.34)
    store.record_prediction("m2", "baseball", "model", 0.40, 0.38, {})
    # market m3: predicted, no snapshot -> skipped
    store.record_prediction("m3", "tennis", "model", 0.55, 0.55, {})


def test_export_schema_and_outcomes(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    _seed(store)
    out = str(tmp_path / "events.csv")
    resolver = _StubResolver({"m2": True})
    summary = export_events_csv(store, out, data_client=resolver)
    assert summary["rows"] == 2
    assert summary["skipped_no_quote"] == 1
    assert resolver.calls == 1  # only m2 needed a lookup

    lines = Path(out).read_text().strip().splitlines()
    header = lines[0].split(",")
    assert header == ["event_id", "domain", "close_time", "resolve_time",
                      "market_prob", "baseline_prob", "outcome"]
    rows = {ln.split(",")[0]: ln.split(",") for ln in lines[1:]}
    # m1: settled bet yes lost -> outcome 0; market prob = mid 0.50; baseline 0.60
    assert rows["m1"][4] == "0.5000"
    assert rows["m1"][5] == "0.6000"
    assert rows["m1"][6] == "0"
    # m2: resolved via venue -> 1
    assert rows["m2"][6] == "1"


def test_roundtrip_through_substrate_loader(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    _seed(store)
    out = str(tmp_path / "events.csv")
    export_events_csv(store, out, data_client=_StubResolver({"m2": False}))

    sys.path.insert(0, str(REPO_ROOT / "substrate"))
    try:
        from ingest import load_events_csv  # substrate/ingest.py
    finally:
        sys.path.pop(0)
    events = load_events_csv(out)
    assert len(events) == 2
    by_id = {e.event_id: e for e in events}
    assert by_id["m1"].outcome == 0
    assert by_id["m2"].outcome == 0
    assert 0.0 < by_id["m1"].market_prob < 1.0
    assert by_id["m1"].domain == "sports"


# ---------------------------------------------------------------- weather

def _weather_service(tmp_path):
    from sportsbot.substrate_bridge import WeatherSnapshotService

    return WeatherSnapshotService(client=object(),
                                  db_path=str(tmp_path / "w.sqlite"))


def _seed_weather(svc, ticker, ts, close_ts, bid=0.45, ask=0.49,
                  outcome=None, settled_ts=None):
    svc.conn.execute(
        "INSERT INTO weather_snapshots (ts, ticker, series, title, yes_bid,"
        " yes_ask, close_ts) VALUES (?,?,?,?,?,?,?)",
        (ts, ticker, ticker.split("-")[0], ticker, bid, ask, close_ts))
    if outcome is not None:
        svc.conn.execute(
            "INSERT OR REPLACE INTO weather_outcomes (ticker, outcome,"
            " settled_ts) VALUES (?,?,?)",
            (ticker, outcome, settled_ts if settled_ts is not None
             else close_ts + 3600))
    svc.conn.commit()


def test_weather_climatology_baseline(tmp_path):
    """Same-strike history sets the baseline; sparse strikes keep 0.5."""
    import csv

    svc = _weather_service(tmp_path)
    day = 86400.0
    t0 = 1_756_000_000.0  # 2025-08-24ish; nearby days-of-year
    # 10 resolved B79.5 days at NYC: 7 hit -> climatology 0.7
    for i in range(10):
        _seed_weather(svc, f"KXHIGHNY-25AUG{10 + i}-B79.5",
                      ts=t0 + i * day, close_ts=t0 + i * day + 3600,
                      outcome=1 if i < 7 else 0)
    # the event under test: same strike, decided AFTER all history settled
    _seed_weather(svc, "KXHIGHNY-25SEP09-B79.5", ts=t0 + 20 * day,
                  close_ts=t0 + 20 * day + 3600)
    # different strike with too little history: stays placeholder
    _seed_weather(svc, "KXHIGHNY-25SEP09-B99.5", ts=t0 + 20 * day,
                  close_ts=t0 + 20 * day + 3600)

    out = tmp_path / "weather.csv"
    summary = svc.export_ingest_csv(str(out))
    rows = {r["event_id"]: r for r in csv.DictReader(open(out))}
    assert rows["KXHIGHNY-25SEP09-B79.5"]["baseline_prob"] == "0.7000"
    assert rows["KXHIGHNY-25SEP09-B99.5"]["baseline_prob"] == "0.5000"
    assert summary["climatology_baseline"] >= 1
    assert summary["placeholder_baseline"] >= 1


def test_weather_climatology_never_uses_future_outcomes(tmp_path):
    """Walk-forward guard: outcomes settled after an event's decision time
    must not leak into that event's baseline."""
    svc = _weather_service(tmp_path)
    day = 86400.0
    t0 = 1_756_000_000.0
    # the event under test is decided at t0 (earliest snapshot)...
    _seed_weather(svc, "KXHIGHCHI-25AUG24-B85.5", ts=t0, close_ts=t0 + 3600,
                  outcome=1, settled_ts=t0 + 7200)
    # ...while ALL same-strike history settles later
    for i in range(1, 11):
        _seed_weather(svc, f"KXHIGHCHI-25AUG{24 + i}-B85.5",
                      ts=t0 + i * day, close_ts=t0 + i * day + 3600,
                      outcome=1, settled_ts=t0 + i * day + 7200)

    out = tmp_path / "weather.csv"
    svc.export_ingest_csv(str(out))
    import csv
    rows = {r["event_id"]: r for r in csv.DictReader(open(out))}
    # earliest event: no prior history -> placeholder, despite 10 later hits
    assert rows["KXHIGHCHI-25AUG24-B85.5"]["baseline_prob"] == "0.5000"
    # latest event: sees the 10 earlier settles -> climatology (clipped 0.98)
    assert rows["KXHIGHCHI-25AUG34-B85.5"]["baseline_prob"] == "0.9800"
