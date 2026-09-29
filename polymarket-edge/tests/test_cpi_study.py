"""CPI nowcast study: bucket wording, event classification, release timing,
nowcast vintage dating and strictness, and the probability arithmetic."""
import math
from datetime import date, datetime, timezone

from src.cpi_study import (
    NowcastMonth, _label_date, bucket_prob, classify, fit_error, nowcast_before,
    parse_bucket, parse_nowcast, release_ts, target_month,
)

INF = math.inf


def test_parse_bucket_covers_every_polymarket_template():
    assert parse_bucket("Will annual inflation increase by 2.7% in January?") == (2.65, 2.75)
    assert parse_bucket("Will annual inflation be 3.4% in May?") == (3.35, 3.45)
    assert parse_bucket("Will Core CPI YoY be 2.5% in May?") == (2.45, 2.55)
    assert parse_bucket("Will monthly inflation increase by 0.2% in January?") == (0.15, 0.25)
    assert parse_bucket("Will monthly inflation decrease by 0.2% in July?") == (-0.25, -0.15)
    assert parse_bucket("Will monthly inflation stay flat (0.0%) in July?") == (-0.05, 0.05)
    assert parse_bucket("Will Core CPI MoM be -0.2% in May?") == (-0.25, -0.15)
    assert parse_bucket("Will annual inflation increase by ≤2.8% in October?") == (-INF, 2.85)
    assert parse_bucket("Will annual inflation increase by ≥3.2% in October?") == (3.15, INF)
    assert parse_bucket("Will monthly inflation increase by 0.4% or more in January?") == (0.35, INF)
    assert parse_bucket("Will monthly inflation increase by 0.1% or less in January?") == (-INF, 0.15)
    assert parse_bucket("Will annual inflation be 3.3% or less in May?") == (-INF, 3.35)
    assert parse_bucket("Will Core CPI MoM be -0.3% or less in May?") == (-INF, -0.25)
    assert parse_bucket("Will monthly inflation decrease by 0.7% or more in July?") == (-INF, -0.65)
    assert parse_bucket("Will UK annual inflation be at least 3.4% in June?") == (3.35, INF)
    assert parse_bucket("Will UK annual inflation be between 2.2% and 2.4% in June?") is None


def test_classify_and_target_month():
    assert classify("March Inflation US - Annual") == ("cpi", "yoy")
    assert classify("July Inflation - Monthly") == ("cpi", "mom")
    assert classify("Core CPI YoY - May 2026") == ("core", "yoy")
    assert classify("Core CPI (ex food and energy) MoM - May 2026") == ("core", "mom")
    assert classify("November Inflation U.K. - Annual") is None
    assert classify("December Inflation Argentina - Annual") is None
    end = datetime(2026, 1, 13, tzinfo=timezone.utc)
    assert target_month("December Inflation US - Annual", end) == "2025-12"   # December data lands in January
    assert target_month("March Inflation US - Annual", datetime(2026, 4, 10, tzinfo=timezone.utc)) == "2026-03"
    assert target_month("Core CPI YoY - May 2026", datetime(2026, 6, 10, tzinfo=timezone.utc)) == "2026-05"


def test_release_is_0830_eastern_with_dst():
    assert release_ts(datetime(2026, 4, 10, tzinfo=timezone.utc)) == datetime(2026, 4, 10, 12, 30, tzinfo=timezone.utc)
    assert release_ts(datetime(2026, 1, 13, 3, 59, tzinfo=timezone.utc)) == datetime(2026, 1, 13, 13, 30, tzinfo=timezone.utc)


def test_label_dates_roll_into_the_next_year_for_december_targets():
    assert _label_date("12/03", "2025-12") == date(2025, 12, 3)
    assert _label_date("01/12", "2025-12") == date(2026, 1, 12)
    assert _label_date("08/26", "2026-07") == date(2026, 8, 26)


def _chart(sub, labels, series):
    return {"chart": {"subcaption": sub},
            "categories": [{"category": [{"label": lab} for lab in labels]}],
            "dataset": [{"seriesname": name, "data": [{"value": v} for v in vals]} for name, vals in series.items()]}


def test_parse_nowcast_skips_marker_labels_and_keeps_the_actual():
    ch = _chart("2026-7", ["07/01", "07/02", "PCE Jul", "08/12"], {
        "CPI Inflation": ["0.10", "0.12", "0.12", "0.09"],
        "Actual CPI Inflation": ["", "", "", "0.0737"],
        "Core CPI Inflation": ["0.2", "", "", "0.21"],
    })
    out = parse_nowcast([ch], "mom")
    nm = out[("cpi", "2026-07")]
    assert nm.kind == "mom" and nm.actual == 0.0737
    assert nm.vintages == [(date(2026, 7, 1), 0.10), (date(2026, 7, 2), 0.12), (date(2026, 8, 12), 0.09)]
    assert out[("core", "2026-07")].vintages[-1] == (date(2026, 8, 12), 0.21)
    assert out[("core", "2026-07")].actual is None


def test_nowcast_before_uses_only_vintages_dated_before_the_horizon_day():
    nm = NowcastMonth("cpi", "mom", "2026-07", [(date(2026, 8, 10), 0.1), (date(2026, 8, 11), 0.2),
                                                (date(2026, 8, 12), 0.3)], 0.07)
    assert nowcast_before(nm, datetime(2026, 8, 11, 12, 30, tzinfo=timezone.utc)) == (date(2026, 8, 10), 0.1)
    assert nowcast_before(nm, datetime(2026, 8, 12, 11, 30, tzinfo=timezone.utc)) == (date(2026, 8, 11), 0.2)
    assert nowcast_before(nm, datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)) is None


def test_bucket_prob_and_error_fit():
    assert abs(bucket_prob(0.0, 1.0, -INF, INF) - 1.0) < 1e-12
    assert abs(bucket_prob(0.0, 1.0, -INF, 0.0) - 0.5) < 1e-12
    p = bucket_prob(2.70, 0.10, 2.65, 2.75)
    assert abs(p - (2 * 0.6915 - 1)) < 1e-3                 # +-0.5 sigma around the mean
    assert bucket_prob(2.70, 0.10, 3.15, INF) < 1e-4
    nowcasts = {}
    for i in range(24):
        month = f"20{20 + i // 12:02d}-{i % 12 + 1:02d}"
        nowcasts[("cpi", "mom", month)] = NowcastMonth("cpi", "mom", month, [(date(2020, 1, 1), 0.2)],
                                                       0.2 + (0.1 if i % 2 else -0.1))
    nowcasts[("cpi", "mom", "2025-03")] = NowcastMonth("cpi", "mom", "2025-03", [(date(2025, 4, 1), 0.0)], 5.0)
    bias, sigma, n = fit_error(nowcasts, "cpi", "mom")
    assert n == 24 and abs(bias) < 1e-12 and abs(sigma - 0.1021) < 1e-3   # the 2025 outlier is out of sample
    assert fit_error(nowcasts, "core", "yoy")[2] == 0
