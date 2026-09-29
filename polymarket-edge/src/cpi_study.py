"""Does the Cleveland Fed inflation nowcast lead Polymarket's CPI buckets?

    python -m src.cpi_study            # collect + analyse
    python -m src.cpi_study --analyse  # re-run on the cached CSV

The one edge class with evidence behind it is a public reference that leads
the market (fed-funds futures vs Fed decision buckets). CPI is the nearest
candidate: Polymarket lists one-decimal buckets on each month's headline and
core CPI (year-over-year and month-over-month), and the Cleveland Fed
publishes a daily nowcast of the same four numbers. Its chart data files
carry every daily vintage per target month since 2013 together with the
released actual, so the nowcast's own error distribution is measurable
out-of-sample and a bucket probability follows with no free parameter.

For every settled US bucket: the nowcast as of the calendar day before the
horizon, its implied bucket probability under a normal with the sigma fitted
on 2013-2024, the Polymarket price 24h and 1h before the 08:30 ET release,
and the outcome. Then the same three tests as the sportsbook study: paired
Brier, a two-source regression, and buying the side the nowcast likes more
than the price after a 1c spread. Plus the Fed-style rule (a bucket at
>= 0.90 a day out) and a lead-lag check of whether the price moves toward
the nowcast between 24h and 1h.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .calibration_study import yes_price_before
from .http_util import retrying_session
from .sportsbook_study import ols, paired_brier_diff

log = logging.getLogger(__name__)

GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
NOWCAST = ("https://www.clevelandfed.org/-/media/files/webcharts/inflationnowcasting/"
           "nowcast_{kind}.json?sc_lang=en")
KINDS = {"mom": "month", "yoy": "year"}
SERIES = {"CPI Inflation": "cpi", "Core CPI Inflation": "core"}
ACTUAL = {"Actual CPI Inflation": "cpi", "Actual Core CPI Inflation": "core"}
HORIZONS_H = (24, 1)
FIT_BEFORE = "2025-01"          # nowcast error sigma is fitted on months before this
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]


# ---------------------------------------------------------------- nowcast

@dataclass
class NowcastMonth:
    series: str             # cpi | core
    kind: str               # mom | yoy
    month: str              # YYYY-MM target month
    vintages: list[tuple[date, float]]   # (publication date, nowcast), ascending
    actual: float | None


def _label_date(label: str, target: str) -> date:
    """Chart labels are mm/dd inside a chart whose subcaption is the target
    month; labels in an earlier calendar month than the target belong to the
    following year (a December target is nowcast into January)."""
    mm, dd = (int(x) for x in label.split("/"))
    ty, tm = (int(x) for x in target.split("-"))
    return date(ty + (1 if mm < tm else 0), mm, dd)


def parse_nowcast(charts: list[dict], kind: str) -> dict[tuple[str, str], NowcastMonth]:
    out = {}
    for ch in charts:
        sub = ch.get("chart", {}).get("subcaption", "")
        if not re.match(r"^\d{4}-\d{1,2}$", sub):
            continue
        y, m = sub.split("-")
        target = f"{int(y):04d}-{int(m):02d}"
        labels = [c["label"] for c in ch["categories"][0]["category"]]
        actuals: dict[str, float] = {}
        vint: dict[str, list[tuple[date, float]]] = defaultdict(list)
        for s in ch.get("dataset", []):
            name = s.get("seriesname", "")
            vals = [d.get("value") for d in s.get("data", [])]
            if name in ACTUAL:
                nz = [float(v) for v in vals if v not in ("", None)]
                if nz:
                    actuals[ACTUAL[name]] = nz[-1]
            elif name in SERIES:
                for lab, v in zip(labels, vals):
                    # release-day markers ("PCE Jul", "CPI Aug") sit among the
                    # mm/dd labels; they carry no dated vintage
                    if v not in ("", None) and re.match(r"^\d{1,2}/\d{1,2}$", lab):
                        vint[SERIES[name]].append((_label_date(lab, target), float(v)))
        for series, points in vint.items():
            out[(series, target)] = NowcastMonth(series=series, kind=kind, month=target,
                                                vintages=sorted(points),
                                                actual=actuals.get(series))
    return out


def load_nowcasts(http, cache_dir: Path) -> dict[tuple[str, str, str], NowcastMonth]:
    """(series, kind, month) -> NowcastMonth for both kinds, cached on disk."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for kind, name in KINDS.items():
        path = cache_dir / f"nowcast_{name}.json"
        if not path.exists():
            r = http.get(NOWCAST.format(kind=name), headers={"User-Agent": "Mozilla/5.0"}, timeout=120)
            r.raise_for_status()
            path.write_bytes(r.content)
        for (series, month), nm in parse_nowcast(json.loads(path.read_text()), kind).items():
            out[(series, kind, month)] = nm
    log.info("cleveland fed: %d series-months of nowcasts", len(out))
    return out


def nowcast_before(nm: NowcastMonth, ts: datetime) -> tuple[date, float] | None:
    """Last vintage dated strictly before the horizon's calendar day: a
    vintage is treated as usable only from the day after its date."""
    cutoff = ts.date()
    prior = [(d, v) for d, v in nm.vintages if d < cutoff]
    return prior[-1] if prior else None


def fit_error(nowcasts: dict, series: str, kind: str, before: str = FIT_BEFORE) -> tuple[float, float, int]:
    """(bias, sigma, n) of actual - final vintage over months before `before`."""
    errs = []
    for (s, k, month), nm in nowcasts.items():
        if s != series or k != kind or month >= before or nm.actual is None or not nm.vintages:
            continue
        errs.append(nm.actual - nm.vintages[-1][1])
    n = len(errs)
    if n < 10:
        return 0.0, float("nan"), n
    mean = sum(errs) / n
    var = sum((e - mean) ** 2 for e in errs) / (n - 1)
    return mean, math.sqrt(var), n


def normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bucket_prob(mu: float, sigma: float, lo: float, hi: float) -> float:
    """P(lo <= X < hi) for X ~ N(mu, sigma); lo/hi may be +-inf."""
    a = 0.0 if lo == -math.inf else normal_cdf((lo - mu) / sigma)
    b = 1.0 if hi == math.inf else normal_cdf((hi - mu) / sigma)
    return max(0.0, min(1.0, b - a))


# ---------------------------------------------------------------- polymarket buckets

_NUM = r"(-?\d+(?:\.\d+)?)"


def parse_bucket(question: str) -> tuple[float, float] | None:
    """(lo, hi) of the published one-decimal value the bucket pays on, in the
    same units as the nowcast (percent). Half-way rounding edges: a bucket
    'be 2.1%' pays for 2.05 <= x < 2.15; 'or less' / '<=' is open below;
    'or more' / '>=' / 'at least' is open above."""
    q = question.lower().replace("≤", "<=").replace("≥", ">=")
    if " between " in q:
        return None
    sign = -1.0 if "decrease by" in q else 1.0

    def bounds(v: float, tail: str | None) -> tuple[float, float]:
        # "decrease by 0.7% or more" is a bigger fall, i.e. open BELOW
        if tail is None:
            lo, hi = v - 0.05, v + 0.05
        elif (tail == "less") == (sign > 0):
            lo, hi = -math.inf, v + 0.05
        else:
            lo, hi = v - 0.05, math.inf
        return (round(lo, 4) if lo != -math.inf else lo, round(hi, 4) if hi != math.inf else hi)

    m = re.search(rf"(<=|>=)\s*{_NUM}%", q)
    if m:
        return bounds(sign * float(m.group(2)), "less" if m.group(1) == "<=" else "more")
    m = re.search(rf"{_NUM}%\s*or\s*(less|more)", q)
    if m:
        return bounds(sign * float(m.group(1)), m.group(2))
    m = re.search(rf"at least\s*{_NUM}%", q)
    if m:
        return bounds(sign * float(m.group(1)), "more")
    m = re.search(rf"\(?{_NUM}%\)?", q)
    if not m:
        return None
    return bounds(sign * float(m.group(1)), None)


def classify(title: str) -> tuple[str, str] | None:
    """(series, kind) from an event title, or None for non-US / unknown."""
    t = title.lower()
    if "uk" in t.split() or "u.k." in t or "argentina" in t or "euro" in t:
        return None
    series = "core" if "core" in t else "cpi"
    if "annual" in t or "yoy" in t:
        return series, "yoy"
    if "monthly" in t or "mom" in t:
        return series, "mom"
    return None


def target_month(title: str, end: datetime) -> str | None:
    t = title.lower()
    for i, name in enumerate(MONTHS):
        if re.search(rf"\b{name}\b", t):
            month = i + 1
            year = end.year - 1 if month > end.month else end.year
            m = re.search(r"\b(20\d\d)\b", title)
            if m:
                year = int(m.group(1))
            return f"{year:04d}-{month:02d}"
    return None


def _eastern_offset(d: date) -> int:
    """UTC offset in hours for US Eastern on date d (DST second Sunday of
    March to first Sunday of November)."""
    def nth_sunday(year, month, n):
        first = date(year, month, 1)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))
    start, end = nth_sunday(d.year, 3, 2), nth_sunday(d.year, 11, 1)
    return -4 if start <= d < end else -5


def release_ts(end: datetime) -> datetime:
    """BLS releases CPI at 08:30 Eastern on the event's end date."""
    d = end.date()
    return datetime(d.year, d.month, d.day, 8, 30, tzinfo=timezone.utc) - timedelta(hours=_eastern_offset(d))


@dataclass
class Bucket:
    slug: str
    title: str
    series: str
    kind: str
    month: str
    question: str
    lo: float
    hi: float
    condition_id: str
    yes_token: str
    outcome: int
    volume: float
    release: datetime


def fetch_buckets(http, max_events: int = 600) -> list[Bucket]:
    out, offset = [], 0
    while offset < max_events:
        r = http.get(GAMMA_EVENTS, params={"tag_slug": "cpi", "closed": "true", "limit": 100,
                                           "offset": offset}, timeout=60)
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list) or not batch:
            break
        for ev in batch:
            out.extend(parse_event(ev))
        if len(batch) < 100:
            break
        offset += 100
    log.info("polymarket: %d US CPI buckets across %d events", len(out), len({b.slug for b in out}))
    return out


def parse_event(ev: dict) -> list[Bucket]:
    title = ev.get("title") or ""
    if "inflation" not in title.lower() and "cpi" not in title.lower():
        return []
    ck = classify(title)
    end_s = ev.get("endDate")
    if ck is None or not end_s:
        return []
    end = datetime.fromisoformat(end_s.replace("Z", "+00:00")).astimezone(timezone.utc)
    month = target_month(title, end)
    if month is None:
        return []
    rel = release_ts(end)
    out = []
    for m in ev.get("markets") or []:
        bounds = parse_bucket(m.get("question") or "")
        try:
            prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
            tokens = json.loads(m.get("clobTokenIds") or "[]")
        except (ValueError, TypeError):
            continue
        if bounds is None or len(prices) != 2 or len(tokens) != 2:
            continue
        if not (prices[0] >= 0.99 or prices[0] <= 0.01):
            continue
        out.append(Bucket(slug=ev.get("slug") or "", title=title, series=ck[0], kind=ck[1],
                          month=month, question=m.get("question") or "", lo=bounds[0], hi=bounds[1],
                          condition_id=m.get("conditionId") or "", yes_token=tokens[0],
                          outcome=1 if prices[0] >= 0.99 else 0,
                          volume=float(m.get("volumeNum") or 0), release=rel))
    return out


# ---------------------------------------------------------------- rows

def build_rows(http, buckets: list[Bucket], nowcasts: dict, fits: dict) -> list[dict]:
    rows = []
    for b in buckets:
        nm = nowcasts.get((b.series, b.kind, b.month))
        bias, sigma, n = fits.get((b.series, b.kind), (0.0, float("nan"), 0))
        if nm is None or math.isnan(sigma):
            log.warning("no nowcast for %s %s %s (%s)", b.series, b.kind, b.month, b.slug)
            continue
        market = {"condition_id": b.condition_id, "yes_token": b.yes_token}
        for h in HORIZONS_H:
            ts = b.release - timedelta(hours=h)
            nc = nowcast_before(nm, ts)
            if nc is None:
                continue
            p = yes_price_before(http, market, int(ts.timestamp()))
            if p is None or not (0 < p < 1):
                continue
            rows.append({
                "slug": b.slug, "series": b.series, "kind": b.kind, "month": b.month,
                "question": b.question, "lo": b.lo, "hi": b.hi, "horizon_h": h,
                "release": b.release.isoformat(), "nowcast_date": nc[0].isoformat(),
                "nowcast": round(nc[1], 4), "sigma": round(sigma, 4),
                "nowcast_prob": round(bucket_prob(nc[1] + bias, sigma, b.lo, b.hi), 4),
                "pm_price": round(p, 4), "outcome": b.outcome, "volume": round(b.volume),
                "actual": "" if nm.actual is None else round(nm.actual, 4),
            })
    return rows


# ---------------------------------------------------------------- analysis

def brier(rows, key):
    return sum((r[key] - r["outcome"]) ** 2 for r in rows) / len(rows)


def side_strategy(rows: list[dict], threshold: float, spread: float = 0.01) -> dict:
    """Buy YES when the nowcast probability exceeds the price by `threshold`,
    buy NO when the price exceeds the nowcast by it; hold to settlement."""
    rets = []
    for r in rows:
        gap = r["nowcast_prob"] - r["pm_price"]
        if gap >= threshold:
            rets.append(r["outcome"] / (r["pm_price"] + spread) - 1)
        elif gap <= -threshold:
            rets.append((1 - r["outcome"]) / (1 - r["pm_price"] + spread) - 1)
    if not rets:
        return {"n": 0}
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / max(1, len(rets) - 1)
    return {"n": len(rets), "mean_ret": mean, "se": math.sqrt(var / len(rets)),
            "hit": sum(1 for x in rets if x > 0) / len(rets)}


def favorite_rule(rows: list[dict], threshold: float = 0.90, spread: float = 0.01) -> dict:
    picks = [r for r in rows if r["pm_price"] >= threshold]
    if not picks:
        return {"n": 0}
    rets = [r["outcome"] / (r["pm_price"] + spread) - 1 for r in picks]
    return {"n": len(picks), "hit": sum(r["outcome"] for r in picks) / len(picks),
            "mean_ret": sum(rets) / len(rets)}


def lead_lag(rows: list[dict]) -> tuple[float, float, int] | None:
    by = {(r["slug"], r["question"], r["horizon_h"]): r for r in rows}
    pts = []
    for (slug, q, h), r in by.items():
        if h != 24:
            continue
        later = by.get((slug, q, 1))
        if later:
            pts.append({"gap": r["nowcast_prob"] - r["pm_price"],
                        "outcome": later["pm_price"] - r["pm_price"]})
    if len(pts) < 10:
        return None
    b, t = ols(pts, ("gap",))["gap"]
    return b, t, len(pts)


def analyse(rows: list[dict], fits: dict, out=sys.stdout) -> None:
    for (series, kind), (bias, sigma, n) in sorted(fits.items()):
        print(f"nowcast error {series} {kind}: bias {bias:+.3f}, sigma {sigma:.3f} pct-pts, "
              f"fit on {n} months before {FIT_BEFORE}", file=out)
    mism = {(r["slug"]) for r in rows if r["actual"] != "" and r["outcome"] == 1
            and not (r["lo"] <= round(float(r["actual"]), 1) + 1e-9 and round(float(r["actual"]), 1) < r["hi"] + 1e-9)}
    print(f"events whose winning bucket disagrees with the rounded Cleveland actual: {len(mism)} "
          f"{sorted(mism)[:5]}", file=out)
    for h in HORIZONS_H:
        hr = [r for r in rows if r["horizon_h"] == h]
        if len(hr) < 20:
            continue
        n_ev = len({r["slug"] for r in hr})
        d, lo, hi = paired_brier_diff(hr, "pm_price", "nowcast_prob")
        reg = ols(hr, ("pm_price", "nowcast_prob"))
        print(f"\n=== {h}h before release: n={len(hr)} buckets / {n_ev} events ===", file=out)
        print(f"Brier polymarket {brier(hr, 'pm_price'):.4f} vs nowcast {brier(hr, 'nowcast_prob'):.4f} "
              f"| diff {d:+.4f} [{lo:+.4f},{hi:+.4f}] | beta_pm {reg['pm_price'][0]:+.2f} "
              f"(t {reg['pm_price'][1]:+.1f}), beta_nowcast {reg['nowcast_prob'][0]:+.2f} "
              f"(t {reg['nowcast_prob'][1]:+.1f})", file=out)
        for (series, kind) in sorted({(r["series"], r["kind"]) for r in hr}):
            sub = [r for r in hr if r["series"] == series and r["kind"] == kind]
            if len(sub) >= 20:
                d2, lo2, hi2 = paired_brier_diff(sub, "pm_price", "nowcast_prob", n_boot=500)
                print(f"    {series} {kind}: n={len(sub)} Brier pm {brier(sub, 'pm_price'):.4f} vs nowcast "
                      f"{brier(sub, 'nowcast_prob'):.4f} diff {d2:+.4f} [{lo2:+.4f},{hi2:+.4f}]", file=out)
        for thr in (0.05, 0.10, 0.20):
            s = side_strategy(hr, thr)
            if s["n"]:
                print(f"    take the nowcast's side when |gap| >= {thr:.2f}: n={s['n']:3d} hit {s['hit']:.3f} "
                      f"return {s['mean_ret']:+.3f}/$1 (se {s['se']:.3f})", file=out)
        f = favorite_rule(hr)
        if f["n"]:
            print(f"    Fed-style rule, bucket priced >= 0.90: n={f['n']} hit {f['hit']:.3f} "
                  f"return {f['mean_ret']:+.4f}/$1", file=out)
    ll = lead_lag(rows)
    if ll:
        print(f"\nlead-lag 24h -> 1h: price move on (nowcast - price) gap: beta {ll[0]:+.2f} "
              f"(t {ll[1]:+.1f}), n={ll[2]}", file=out)


FIELDS = ["slug", "series", "kind", "month", "question", "lo", "hi", "horizon_h", "release",
          "nowcast_date", "nowcast", "sigma", "nowcast_prob", "pm_price", "outcome", "volume",
          "actual"]


def load_rows(path: Path) -> list[dict]:
    rows = []
    for r in csv.DictReader(path.open()):
        for k in ("lo", "hi", "nowcast", "sigma", "nowcast_prob", "pm_price", "volume"):
            r[k] = float(r[k])
        r["horizon_h"] = int(r["horizon_h"])
        r["outcome"] = int(r["outcome"])
        rows.append(r)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Cleveland Fed nowcast vs Polymarket CPI buckets")
    parser.add_argument("--analyse", action="store_true")
    parser.add_argument("--csv", default="docs/cpi_rows.csv")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = Path(__file__).resolve().parent.parent
    http = retrying_session()
    nowcasts = load_nowcasts(http, root / "data" / "cleveland")
    fits = {(s, k): fit_error(nowcasts, s, k) for s in ("cpi", "core") for k in KINDS}
    csv_path = root / args.csv
    if not args.analyse:
        buckets = fetch_buckets(http)
        rows = build_rows(http, buckets, nowcasts, fits)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        log.info("wrote %d rows to %s", len(rows), csv_path)
    rows = load_rows(csv_path)
    print(f"{len(rows)} rows, {len({r['slug'] for r in rows})} events")
    analyse(rows, fits)


if __name__ == "__main__":
    main()
