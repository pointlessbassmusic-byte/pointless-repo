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
