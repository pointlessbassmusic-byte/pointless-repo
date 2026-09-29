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

from .calibration_study import yes_price_before
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


def resolution_time(description: str) -> datetime | None:
    m = _RES_RE.search(description or "")
    if not m:
        return None
    dd, mon, yy, hh, mi = m.groups()
    month = _MONTHS.get(mon.lower())
    if month is None:
        return None
    local = datetime(2000 + int(yy), month, int(dd), int(hh), int(mi), tzinfo=timezone.utc)
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
        res = resolution_time(m.get("description") or "")
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


def fetch_digitals(http, since: str = "2024-03-01", window_days: int = 7) -> list[Digital]:
    """Every settled BTC/ETH digital under the bitcoin and ethereum tags.
    Gamma refuses offsets past ~2100 and the bitcoin tag alone has thousands
    of closed events, so discovery is paged inside weekly end-date windows,
    which the API accepts as date-only end_date_min / end_date_max."""
    seen, out = set(), []
    lo = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    today = datetime.now(timezone.utc) + timedelta(days=1)
    while lo < today:
        hi = lo + timedelta(days=window_days)
        for tag in ("bitcoin", "ethereum"):
            offset = 0
            while offset < 2100:
                r = http.get(GAMMA_EVENTS, params={
                    "tag_slug": tag, "closed": "true", "limit": 100, "offset": offset,
                    "end_date_min": lo.date().isoformat(), "end_date_max": hi.date().isoformat()},
                    timeout=60)
                if r.status_code == 422:
                    break
                r.raise_for_status()
                batch = r.json()
                if not isinstance(batch, list) or not batch:
                    break
                for ev in batch:
                    for d in parse_event(ev):
                        if d.condition_id not in seen:
                            seen.add(d.condition_id)
                            out.append(d)
                if len(batch) < 100:
                    break
                offset += 100
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
    pts: list[tuple[int, float]] = json.loads(path.read_text()) if path.exists() else []
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
    pts: list[tuple[int, float]] = json.loads(path.read_text()) if path.exists() else []
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
        p = yes_price_before(http, market, ts)
        if p is None or not (0 < p < 1):
            continue
        tau = h / HOURS_PER_YEAR
        rows.append({
            "slug": d.slug, "asset": d.asset, "question": d.question, "lo": d.lo, "hi": d.hi,
            "resolves": d.resolves.isoformat(), "horizon_h": h, "spot": round(s, 2),
            "dvol": round(iv, 2), "rv": "" if rv is None else round(rv * 100, 2),
            "pm_price": round(p, 4),
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
          "pm_price", "dvol_prob", "rv_prob", "outcome", "volume"]


def load_rows(path: Path) -> list[dict]:
    rows = []
    for r in csv.DictReader(path.open()):
        for k in ("lo", "hi", "spot", "dvol", "pm_price", "dvol_prob", "volume"):
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
        digitals = fetch_digitals(http)
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
