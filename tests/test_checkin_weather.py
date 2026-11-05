"""scripts/checkin/weather_export.py: the daily NWS cohort scorer, rebuilt
2026-11-05 after a container reset lost the uncommitted original. It must
reproduce the 2026-11-04 published numbers from the committed cohort CSV."""

import csv
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("weather_export", ROOT / "scripts" / "checkin" / "weather_export.py")
wx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wx)

COMPLETE_1104 = (
    "2026-09-21 2026-09-22 2026-09-23 2026-09-26 2026-09-27 2026-09-28 2026-09-29 2026-09-30 "
    "2026-10-02 2026-10-03 2026-10-06 2026-10-07 2026-10-08 2026-10-09 2026-10-10 2026-10-11 "
    "2026-10-12 2026-10-13 2026-10-14 2026-10-15 2026-10-19 2026-10-20 2026-10-21 2026-10-22 "
    "2026-10-23 2026-10-24 2026-10-25 2026-10-26 2026-10-27 2026-10-28 2026-10-29 2026-10-30 "
    "2026-10-31 2026-11-01 2026-11-02").split()


def test_reproduces_the_published_2026_11_04_cohort_numbers():
    with open(ROOT / "substrate" / "reports" / "weather_nws_cohort_2026-11-04.csv") as fh:
        rows = list(csv.DictReader(fh))
    s = wx.score(rows, set(COMPLETE_1104))
    assert len(s["dates"]) == 35
    assert s["per-date clim-NWS"] == {"n": 35, "mean": 0.0264, "sd": 0.0437, "se": 0.0074, "t": 3.58, "pos": 29}
    assert s["per-date mkt-NWS"]["mean"] == -0.0767 and s["per-date mkt-NWS"]["t"] == -10.88
    assert s["per-date mkt-NWS"]["pos"] == 0
    assert s["station-day clim-NWS"]["n"] == 242 and s["station-day clim-NWS"]["t"] == 4.62


def test_history_is_kept_and_only_new_tickers_appended(tmp_path):
    hist = [{"ticker": "A", "series": "S", "target_date": "2026-01-01", "lead_days": "0.5",
             "market": "0.4", "nws": "0.3", "climatology": "0.2", "outcome": "1"}]
    rec = {"A": {**hist[0], "market": 0.45, "nws": 0.3, "climatology": 0.2, "outcome": 1}}
    checked, bad = wx.validate(hist, rec)
    assert checked == 1 and bad == [("A", "market", "0.4", 0.45)]   # reported, never overwritten
    assert wx.validate(hist, {}) == (0, [])
