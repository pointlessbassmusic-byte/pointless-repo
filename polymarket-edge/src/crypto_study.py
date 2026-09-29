"""Does Deribit's option market lead Polymarket's BTC / ETH price digitals?

    python -m src.crypto_study            # collect + analyse
    python -m src.crypto_study --analyse  # re-run on the cached CSV

The one reference that has beaten a prediction market in this repo is itself
a deep market pricing the identical event (fed-funds futures vs Fed decision
buckets). Crypto is the cleanest place to test that property again:
Polymarket lists fixed-time digitals ("price of Bitcoin between $X and $Y on
<date> at 5 PM ET", "greater than", "less than", "above $X on <date>") that
settle on a Binance one-minute close, and Deribit's option market publishes
DVOL, an implied-volatility index whose hourly history is free, alongside
the perpetual's hourly price. A lognormal with zero drift turns spot and
DVOL into a probability for every strike with no fitted parameter.

For every settled BTC / ETH digital: the Polymarket price at 1h / 6h / 24h
before the resolution time, the Deribit-implied probability from spot and
DVOL as of the same hour, a realised-volatility version (trailing 30-day
hourly returns) as a non-market control, and the outcome. Then the same
tests as the other studies: paired Brier, two-source regression, taking the
reference's side after a 1c spread, the Fed-style >= 0.90 rule, and a
lead-lag check.

Path-dependent markets ("reach", "hit", "dip to") are excluded: they are
touch options, not digitals, and a lognormal terminal distribution does not
price them.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import re
import sys
from bisect import bisect_right
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .calibration_study import TRADES
from .cpi_study import _eastern_offset
from .http_util import retrying_session
from .sportsbook_study import ols, paired_brier_diff

log = logging.getLogger(__name__)

GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
DERIBIT = "https://www.deribit.com/api/v2/public"
TAGS = ("bitcoin", "ethereum", "crypto")
ASSETS = {"bitcoin": "BTC", "ethereum": "ETH"}
HORIZONS_H = (1, 6, 24)
HOURS_PER_YEAR = 24 * 365.25
RV_WINDOW_H = 24 * 30
FRESH_MIN = 10  # a print this recent is a price you could plausibly have traded near

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
# "BTCUSDT 15 May '25 17:00 in the ET timezone"
_RES_RE = re.compile(r"(\d{1,2}) ([A-Za-z]{3}) '(\d{2}) (\d{1,2}):(\d{2}) in the ET timezone")
_MONEY = r"\$([\d,]+(?:\.\d+)?)\s*([kK])?"


def _money(num: str, k: str | None) -> float:
    v = float(num.replace(",", ""))
    return v * 1000 if k else v


def parse_question(q: str) -> tuple[str, float, float] | None:
    """(asset, lo, hi) for a fixed-time digital; None for touch markets and
    anything else. Bounds are on the settlement price; a 'between' bucket
    pays for lo <= price < hi (ties go to the higher bracket by the rules)."""
    t = q.lower()
    asset = next((a for a in ASSETS if a in t), None)
    if asset is None or re.search(r"\b(reach|hit|dip)\b", t):
        return None
    inf = math.inf
    m = re.search(rf"between {_MONEY} and {_MONEY}", q, re.I)
    if m:
        a, b = _money(m.group(1), m.group(2)), _money(m.group(3), m.group(4))
        return asset, min(a, b), max(a, b)
    m = re.search(rf"(greater than|above|more than|over) {_MONEY}", q, re.I)
    if m:
        return asset, _money(m.group(2), m.group(3)), inf
    m = re.search(rf"(less than|below|under) {_MONEY}", q, re.I)
    if m:
        return asset, -inf, _money(m.group(2), m.group(3))
    return None


_TIME_RE = re.compile(r"(\d{1,2}):(\d{2}) in the ET timezone")
_TITLE_DATE_RE = re.compile(r"\bon ([A-Za-z]+) (\d{1,2})(?:, (\d{4}))?\b")


def resolution_time(description: str, question: str = "", end_date: str = "") -> datetime | None:
    """Settlement instant in UTC. Two wordings exist: until May 2025 the
    description carried the full stamp ("15 May '25 17:00 in the ET timezone");
    since then it says "12:00 in the ET timezone (noon) on the date specified
    in the title" and the title says "on September 10". The second form takes
    the year from the event's endDate and is accepted only if it lands within
    two hours of that endDate, so a misread date cannot slip through."""
    m = _RES_RE.search(description or "")
    if m:
        dd, mon, yy, hh, mi = m.groups()
        month = _MONTHS.get(mon.lower())
        if month is None:
            return None
        return _eastern_to_utc(datetime(2000 + int(yy), month, int(dd), int(hh), int(mi)))
    mt = _TIME_RE.search(description or "")
    md = _TITLE_DATE_RE.search(question or "")
    if not (mt and md and end_date):
        return None
    month = _MONTHS.get(md.group(1).lower()[:3])
    if month is None or len(md.group(1)) < 3:
        return None
    try:
        end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
    except ValueError:
        return None
    year = int(md.group(3)) if md.group(3) else end.year
    try:
        res = _eastern_to_utc(datetime(year, month, int(md.group(2)), int(mt.group(1)), int(mt.group(2))))
    except ValueError:
        return None
    if abs((res - end).total_seconds()) > 2 * 3600:
        return None
    return res


def _eastern_to_utc(local_naive: datetime) -> datetime:
    local = local_naive.replace(tzinfo=timezone.utc)
    return local - timedelta(hours=_eastern_offset(local.date()))


@dataclass
class Digital:
    slug: str
    asset: str
    question: str
    lo: float
    hi: float
    resolves: datetime
    condition_id: str
    yes_token: str
    outcome: int
    volume: float


def parse_event(ev: dict) -> list[Digital]:
    out = []
    for m in ev.get("markets") or []:
        parsed = parse_question(m.get("question") or "")
        if parsed is None:
            continue
        res = resolution_time(m.get("description") or "", m.get("question") or "",
                              ev.get("endDate") or "")
        if res is None:
            continue
        try:
            prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
            tokens = json.loads(m.get("clobTokenIds") or "[]")
        except (ValueError, TypeError):
            continue
        if len(prices) != 2 or len(tokens) != 2 or not (prices[0] >= 0.99 or prices[0] <= 0.01):
            continue
        asset, lo, hi = parsed
        out.append(Digital(slug=ev.get("slug") or "", asset=ASSETS[asset], question=m["question"],
                           lo=lo, hi=hi, resolves=res, condition_id=m.get("conditionId") or "",
                           yes_token=tokens[0], outcome=1 if prices[0] >= 0.99 else 0,
                           volume=float(m.get("volumeNum") or 0)))
    return out


def _digital_to_dict(d: Digital) -> dict:
    out = d.__dict__.copy()
    out["resolves"] = d.resolves.isoformat()
    return out


def _digital_from_dict(o: dict) -> Digital:
    o = dict(o)
    o["resolves"] = datetime.fromisoformat(o["resolves"])
    return Digital(**o)


def _window_digitals(http, tag: str, lo: datetime, hi: datetime) -> list[Digital] | None:
    """One tag's settled digitals ending inside [lo, hi). Returns None when
    Gamma keeps answering 5xx after the session's retries, so the caller
    can skip the window instead of losing the whole run (2026-09-29: one
    offset of the bitcoin tag 500'd persistently and killed a 13-minute
    discovery pass)."""
    out, offset = [], 0
    while offset < 2100:
        r = http.get(GAMMA_EVENTS, params={
            "tag_slug": tag, "closed": "true", "limit": 100, "offset": offset,
            "end_date_min": lo.date().isoformat(), "end_date_max": hi.date().isoformat()},
            timeout=60)
        if r.status_code == 422:
            break
        if r.status_code >= 500:
            log.warning("gamma %s: %s at %s..%s offset %d, skipping window",
                        r.status_code, tag, lo.date(), hi.date(), offset)
            return None
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list) or not batch:
            break
        for ev in batch:
            out.extend(parse_event(ev))
        if len(batch) < 100:
            break
        offset += 100
    return out


def fetch_digitals(http, since: str = "2024-03-01", window_days: int = 7,
                   cache: Path | None = None) -> list[Digital]:
    """Every settled BTC/ETH digital under the bitcoin and ethereum tags.
    Gamma refuses offsets past ~2100 and the bitcoin tag alone has thousands
    of closed events, so discovery is paged inside weekly end-date windows,
    which the API accepts as date-only end_date_min / end_date_max. Windows
    that ended more than a day ago are cached (keyed by window start) so a
    rerun only pages the recent ones."""
    cache_path = cache / "crypto_digitals.json" if cache else None
    done: dict[str, list[dict]] = {}
    if cache_path and cache_path.exists():
        done = json.loads(cache_path.read_text())
    seen, out = set(), []
    lo = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    while lo < now + timedelta(days=1):
        hi = lo + timedelta(days=window_days)
        key = lo.date().isoformat()
        if key in done:
            found = [_digital_from_dict(o) for o in done[key]]
        else:
            found, complete = [], True
            for tag in ("bitcoin", "ethereum"):
                got = _window_digitals(http, tag, lo, hi)
                if got is None:
                    complete = False
                else:
                    found.extend(got)
            if complete and hi < now - timedelta(days=1) and cache_path:
                done[key] = [_digital_to_dict(d) for d in found]
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(done))
            log.info("gamma %s..%s: %d digitals%s", lo.date(), hi.date(), len(found),
                     "" if complete else " (partial)")
        for d in found:
            if d.condition_id not in seen:
                seen.add(d.condition_id)
                out.append(d)
        lo = hi
    log.info("polymarket: %d settled BTC/ETH digitals", len(out))
    return out


# ---------------------------------------------------------------- deribit

class HourlySeries:
    """Hourly (ts_seconds, value) lookup: value at the last hour <= ts."""

    def __init__(self, points: list[tuple[int, float]]):
        pts = sorted(set(points))
        self.ts = [p[0] for p in pts]
        self.vals = [p[1] for p in pts]

    def at(self, ts: int) -> float | None:
        i = bisect_right(self.ts, ts) - 1
        if i < 0 or ts - self.ts[i] > 3 * 3600:
            return None
        return self.vals[i]

    def realised_vol(self, ts: int, window_h: int = RV_WINDOW_H) -> float | None:
        """Annualised stdev of hourly log returns over the window ending at ts."""
        j = bisect_right(self.ts, ts)
        i = bisect_right(self.ts, ts - window_h * 3600)
        closes = self.vals[i:j]
        if len(closes) < window_h // 2:
            return None
        rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var * HOURS_PER_YEAR)


def fetch_dvol(http, currency: str, start: int, end: int, cache: Path) -> HourlySeries:
    """Hourly DVOL closes (percent) between start and end (seconds)."""
    path = cache / f"dvol_{currency}.json"
    pts: list[tuple[int, float]] = [tuple(p) for p in json.loads(path.read_text())] if path.exists() else []
    have = {p[0] for p in pts}
    cursor = end * 1000
    while cursor > start * 1000:
        r = http.get(f"{DERIBIT}/get_volatility_index_data", params={
            "currency": currency, "start_timestamp": start * 1000, "end_timestamp": cursor,
            "resolution": 3600}, timeout=60)
        r.raise_for_status()
        res = r.json().get("result", {})
        data = res.get("data") or []
        new = [(int(row[0] // 1000), float(row[4])) for row in data]
        if not new or all(p[0] in have for p in new):
            break
        pts.extend(p for p in new if p[0] not in have)
        have.update(p[0] for p in new)
        cont = res.get("continuation")
        if not cont or cont >= cursor:
            break
        cursor = int(cont)
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(pts)))
    return HourlySeries(pts)


def fetch_spot(http, currency: str, start: int, end: int, cache: Path) -> HourlySeries:
    """Hourly perpetual closes between start and end (seconds), 5000 per page."""
    path = cache / f"spot_{currency}.json"
    pts: list[tuple[int, float]] = [tuple(p) for p in json.loads(path.read_text())] if path.exists() else []
    have = {p[0] for p in pts}
    chunk = 4900 * 3600
    lo = start
    while lo < end:
        hi = min(end, lo + chunk)
        if not all(t in have for t in range(lo - lo % 3600, hi, 3600)):
            r = http.get(f"{DERIBIT}/get_tradingview_chart_data", params={
                "instrument_name": f"{currency}-PERPETUAL", "start_timestamp": lo * 1000,
                "end_timestamp": hi * 1000, "resolution": 60}, timeout=60)
            r.raise_for_status()
            res = r.json().get("result", {})
            for t, c in zip(res.get("ticks") or [], res.get("close") or []):
                ts = int(t // 1000)
                if ts not in have:
                    pts.append((ts, float(c)))
                    have.add(ts)
        lo = hi
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(pts)))
    return HourlySeries(pts)


# ---------------------------------------------------------------- pricing

def normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def prob_above(spot: float, strike: float, sigma: float, tau_years: float) -> float:
    """Zero-drift lognormal P(S_T >= strike)."""
    if strike <= 0:
        return 1.0
    if sigma <= 0 or tau_years <= 0:
        return 1.0 if spot >= strike else 0.0
    d = (math.log(spot / strike) - 0.5 * sigma * sigma * tau_years) / (sigma * math.sqrt(tau_years))
    return normal_cdf(d)


def digital_prob(spot: float, lo: float, hi: float, sigma: float, tau_years: float) -> float:
    p_lo = 1.0 if lo == -math.inf else prob_above(spot, lo, sigma, tau_years)
    p_hi = 0.0 if hi == math.inf else prob_above(spot, hi, sigma, tau_years)
    return max(0.0, min(1.0, p_lo - p_hi))


# ---------------------------------------------------------------- rows

def yes_trade_before(http, market: dict, ts: int) -> tuple[float, int] | None:
    """(YES price, trade timestamp) of the last trade at or before ts. The
    timestamp matters: a print from hours earlier is not a price anyone could
    have traded at, and against a fresh spot it fakes a reference edge."""
    r = http.get(TRADES, params={"market": market["condition_id"], "limit": 3, "end": ts},
                 timeout=60)
    r.raise_for_status()
    trades = r.json()
    if not isinstance(trades, list) or not trades:
        return None
    t = max(trades, key=lambda x: x.get("timestamp", 0))
    p = float(t["price"])
    return (p if t.get("asset") == market["yes_token"] else 1 - p), int(t.get("timestamp", 0))


def sample(http, d: Digital, dvol: HourlySeries, spot: HourlySeries) -> list[dict]:
    rows = []
    t_res = int(d.resolves.timestamp())
    market = {"condition_id": d.condition_id, "yes_token": d.yes_token}
    for h in HORIZONS_H:
        ts = t_res - h * 3600
        s, iv = spot.at(ts), dvol.at(ts)
        if s is None or iv is None:
            continue
        rv = spot.realised_vol(ts)
        got = yes_trade_before(http, market, ts)
        if got is None or not (0 < got[0] < 1):
            continue
        p, t_trade = got
        tau = h / HOURS_PER_YEAR
        rows.append({
            "slug": d.slug, "asset": d.asset, "question": d.question, "lo": d.lo, "hi": d.hi,
            "resolves": d.resolves.isoformat(), "horizon_h": h, "spot": round(s, 2),
            "dvol": round(iv, 2), "rv": "" if rv is None else round(rv * 100, 2),
            "pm_price": round(p, 4), "pm_age_min": round((ts - t_trade) / 60, 1),
            "dvol_prob": round(digital_prob(s, d.lo, d.hi, iv / 100, tau), 4),
            "rv_prob": "" if rv is None else round(digital_prob(s, d.lo, d.hi, rv, tau), 4),
            "outcome": d.outcome, "volume": round(d.volume),
        })
    return rows


# ---------------------------------------------------------------- analysis

def brier(rows, key):
    return sum((r[key] - r["outcome"]) ** 2 for r in rows) / len(rows)


def side_strategy(rows: list[dict], ref: str, threshold: float, spread: float = 0.01) -> dict:
    rets = []
    for r in rows:
        gap = r[ref] - r["pm_price"]
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


def lead_lag(rows: list[dict], ref: str, h_from: int, h_to: int):
    by = {(r["slug"], r["question"], r["horizon_h"]): r for r in rows}
    pts = []
    for (slug, q, h), r in by.items():
        if h != h_from:
            continue
        later = by.get((slug, q, h_to))
        if later:
            pts.append({"gap": r[ref] - r["pm_price"], "outcome": later["pm_price"] - r["pm_price"]})
    if len(pts) < 10:
        return None
    b, t = ols(pts, ("gap",))["gap"]
    return b, t, len(pts)


def analyse(rows: list[dict], out=sys.stdout) -> None:
    rows = [r for r in rows if r["rv_prob"] != ""]
    for h in HORIZONS_H:
        hr = [r for r in rows if r["horizon_h"] == h]
        if len(hr) < 30:
            continue
        n_ev = len({r["slug"] for r in hr})
        print(f"\n=== {h}h before resolution: n={len(hr)} digitals / {n_ev} events ===", file=out)
        for ref, label in (("dvol_prob", "Deribit DVOL implied"), ("rv_prob", "30d realised vol")):
            d, lo, hi = paired_brier_diff(hr, "pm_price", ref)
            reg = ols(hr, ("pm_price", ref))
            print(f"{label:<22} Brier polymarket {brier(hr, 'pm_price'):.4f} vs ref {brier(hr, ref):.4f} "
                  f"| diff {d:+.4f} [{lo:+.4f},{hi:+.4f}] | beta_pm {reg['pm_price'][0]:+.2f} "
                  f"(t {reg['pm_price'][1]:+.1f}), beta_ref {reg[ref][0]:+.2f} (t {reg[ref][1]:+.1f})",
                  file=out)
        for asset in ("BTC", "ETH"):
            sub = [r for r in hr if r["asset"] == asset]
            if len(sub) >= 30:
                d, lo, hi = paired_brier_diff(sub, "pm_price", "dvol_prob", n_boot=500)
                print(f"    {asset}: n={len(sub)} Brier pm {brier(sub, 'pm_price'):.4f} vs dvol "
                      f"{brier(sub, 'dvol_prob'):.4f} diff {d:+.4f} [{lo:+.4f},{hi:+.4f}]", file=out)
        vols = sorted(r["volume"] for r in hr)
        cut = vols[len(vols) // 2]
        for name, sub in (("low-volume half", [r for r in hr if r["volume"] < cut]),
                          ("high-volume half", [r for r in hr if r["volume"] >= cut])):
            if len(sub) >= 30:
                d, lo, hi = paired_brier_diff(sub, "pm_price", "dvol_prob", n_boot=500)
                print(f"    {name} (n={len(sub)}, cut ${cut:,.0f}): diff {d:+.4f} [{lo:+.4f},{hi:+.4f}]", file=out)
        for name, sub in (("fresh print (<= %d min old)" % FRESH_MIN,
                           [r for r in hr if r["pm_age_min"] <= FRESH_MIN]),
                          ("stale print (> 60 min old)", [r for r in hr if r["pm_age_min"] > 60])):
            if len(sub) >= 30:
                d, lo, hi = paired_brier_diff(sub, "pm_price", "dvol_prob", n_boot=500)
                st = side_strategy(sub, "dvol_prob", 0.05)
                print(f"    {name}: n={len(sub)} diff {d:+.4f} [{lo:+.4f},{hi:+.4f}]; DVOL side at "
                      f"|gap| >= 0.05: n={st['n']} return {st['mean_ret']:+.3f}/$1 (se {st['se']:.3f})",
                      file=out)
        ages = sorted(r["pm_age_min"] for r in hr)
        print(f"    last-trade age: median {ages[len(ages) // 2]:.0f} min, 90th "
              f"{ages[int(0.9 * len(ages))]:.0f} min", file=out)
        gap = sorted(abs(r["pm_price"] - r["dvol_prob"]) for r in hr)
        print(f"    |polymarket - dvol implied|: median {gap[len(gap) // 2]:.3f}, 90th "
              f"{gap[int(0.9 * len(gap))]:.3f}", file=out)
        for thr in (0.05, 0.10, 0.20):
            s = side_strategy(hr, "dvol_prob", thr)
            if s["n"]:
                print(f"    take the DVOL side when |gap| >= {thr:.2f}: n={s['n']:4d} hit {s['hit']:.3f} "
                      f"return {s['mean_ret']:+.3f}/$1 (se {s['se']:.3f})", file=out)
        f = favorite_rule(hr)
        if f["n"]:
            print(f"    Fed-style rule, digital priced >= 0.90: n={f['n']} hit {f['hit']:.3f} "
                  f"return {f['mean_ret']:+.4f}/$1", file=out)
    for h_from in (24, 6):
        ll = lead_lag(rows, "dvol_prob", h_from, 1)
        if ll:
            print(f"lead-lag {h_from}h -> 1h: price move on (dvol - price) gap: beta {ll[0]:+.2f} "
                  f"(t {ll[1]:+.1f}), n={ll[2]}", file=out)


FIELDS = ["slug", "asset", "question", "lo", "hi", "resolves", "horizon_h", "spot", "dvol", "rv",
          "pm_price", "pm_age_min", "dvol_prob", "rv_prob", "outcome", "volume"]


def load_rows(path: Path) -> list[dict]:
    rows = []
    for r in csv.DictReader(path.open()):
        for k in ("lo", "hi", "spot", "dvol", "pm_price", "pm_age_min", "dvol_prob", "volume"):
            r[k] = float(r[k])
        for k in ("rv", "rv_prob"):
            r[k] = float(r[k]) if r[k] != "" else ""
        r["horizon_h"] = int(r["horizon_h"])
        r["outcome"] = int(r["outcome"])
        rows.append(r)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Deribit-implied vs Polymarket BTC/ETH digitals")
    parser.add_argument("--analyse", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--csv", default="docs/crypto_rows.csv")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = Path(__file__).resolve().parent.parent
    csv_path = root / args.csv
    if not args.analyse:
        http = retrying_session()
        digitals = fetch_digitals(http, cache=root / "data" / "polymarket")
        by_asset: dict[str, list[Digital]] = defaultdict(list)
        for d in digitals:
            by_asset[d.asset].append(d)
        series = {}
        for asset, ds in by_asset.items():
            t0 = int(min(d.resolves for d in ds).timestamp()) - (RV_WINDOW_H + 48) * 3600
            t1 = int(max(d.resolves for d in ds).timestamp()) + 3600
            series[asset] = (fetch_dvol(http, asset, t0, t1, root / "data" / "deribit"),
                             fetch_spot(http, asset, t0, t1, root / "data" / "deribit"))
            log.info("deribit %s: %d dvol hours, %d spot hours", asset, len(series[asset][0].ts),
                     len(series[asset][1].ts))
        rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(sample, http, d, *series[d.asset]) for d in digitals]
            for i, f in enumerate(as_completed(futs), 1):
                try:
                    rows.extend(f.result())
                except Exception:  # noqa: BLE001 — one market failing must not sink the run
                    log.exception("sampling failed")
                if i % 200 == 0:
                    log.info("sampled %d/%d digitals, %d rows", i, len(digitals), len(rows))
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        log.info("wrote %d rows to %s", len(rows), csv_path)
    rows = load_rows(csv_path)
    print(f"{len(rows)} rows, {len({r['slug'] for r in rows})} events, "
          f"{len({(r['slug'], r['question']) for r in rows})} digitals")
    analyse(rows)


if __name__ == "__main__":
    main()
