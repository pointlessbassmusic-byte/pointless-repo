"""Kalshi hourly crypto markets against live spot: is the book a step
behind the price it settles on?  DATA ONLY — public reads, no orders.

KXBTCD / KXETHD list, every hour, a ladder of "above $K at H:00" binaries
that settle on the 60-second average of the CF Benchmarks real-time index
(BRTI / ETH RTI) before the hour. The index is built from the big spot
venues (Coinbase, Kraken, Bitstamp, ...), so spot is a deep market pricing
almost the identical event, against a thinner prediction-market crowd: the
reference-price property the Fed rule has and every other candidate lacked
(see the repo CLAUDE.md, "What resolved data says about edges"). Never
measured here before 2026-10-14.

Construction, walk-forward and outcome-blind:

* events: settled hourly events of the series, newest first, over `days`;
* strikes: chosen from SPOT AT DECISION START (60 min before close), the
  ones within `band` log-distance of it, never by volume or result
  (both condition on where the price ended);
* book: Kalshi per-minute candlesticks (`yes_bid` / `yes_ask` close),
  stamped by the candle's END (`end_period_ts`): the quote at t is known at t;
* spot: Coinbase 1-minute candles, stamped by their OPEN time by the
  venue, re-keyed here by close (open + 60) — the Deribit lesson: keyed by
  open, "the last close at or before t" is a minute in the future;
* model at t with tau minutes left: p = Phi(ln(S_t / K) / (sigma *
  sqrt(tau - 2/3))), sigma the std of the previous 60 one-minute log
  returns; tau - 2/3 is the variance left in a Brownian path's average
  over the final minute;
* scoring at tau in TAUS: Brier of the model and of the book mid against
  the settled result on two-sided books with the mid inside 0.03-0.97, the
  regression of (outcome - mid) on (model - mid), and the taker rule
  "buy YES at the ask when p - ask - fee > margin" (and the NO mirror at
  1 - bid), paid at settlement, net of Kalshi's quadratic taker fee.
  Errors cluster by event (one hour's strikes share one path).

Run: `python -m src.crypto_hourly_study --series KXBTCD --days 14`
(fetch, cached under data/crypto_hourly/) then `--analyse`.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import statistics
import time
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
COINBASE = "https://api.exchange.coinbase.com"
SPOT_PRODUCT = {"KXBTCD": "BTC-USD", "KXETHD": "ETH-USD"}
TAUS = (30, 15, 10, 5, 2)
KALSHI_TAKER = 0.07
CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "crypto_hourly")


def taker_fee(p: float, rate: float = KALSHI_TAKER) -> float:
    return rate * p * (1.0 - p)


def phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def model_prob(spot: float, strike: float, sigma_min: float, tau_min: float) -> Optional[float]:
    """P(average of the final minute > strike), driftless lognormal."""
    if spot <= 0 or strike <= 0 or sigma_min <= 0 or tau_min <= 2.0 / 3.0:
        return None
    return phi(math.log(spot / strike) / (sigma_min * math.sqrt(tau_min - 2.0 / 3.0)))


def _ts(s) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class SpotSeries:
    """1-minute closes keyed by CLOSE time (venue stamps are open times)."""

    def __init__(self, candles_open_stamped: list[list]):
        rows = sorted((float(c[0]) + 60.0, float(c[4])) for c in candles_open_stamped)
        self.t = [r[0] for r in rows]
        self.p = [r[1] for r in rows]

    def at(self, ts: float) -> Optional[float]:
        i = bisect_right(self.t, ts) - 1
        if i < 0 or ts - self.t[i] > 180:          # no stale prints older than 3 minutes
            return None
        return self.p[i]

    def sigma(self, ts: float, window: int = 60) -> Optional[float]:
        i = bisect_right(self.t, ts) - 1
        if i < window:
            return None
        seg = self.p[i - window:i + 1]
        rets = [math.log(b / a) for a, b in zip(seg, seg[1:]) if a > 0 and b > 0]
        return statistics.pstdev(rets) if len(rets) >= window // 2 else None


# ------------------------------------------------------------------ fetch
class Fetcher:
    def __init__(self, session=None, pause: float = 0.15, sleep=time.sleep):
        if session is None:
            from .http_util import retrying_session
            session = retrying_session()
            session.headers["User-Agent"] = "kalshi-engine-study/1.0"
        self.s = session
        self.pause = pause
        self.sleep = sleep

    def _get(self, url, params=None):
        for wait in (2.0, 4.0, 8.0, 16.0, None):     # public reads only: waiting out a 429 is safe
            r = self.s.get(url, params=params, timeout=30)
            if r.status_code == 429 and wait is not None:
                self.sleep(wait)
                continue
            r.raise_for_status()
            self.sleep(self.pause)
            return r.json()

    def events(self, series: str, days: float, now: float) -> list[dict]:
        out, cursor = [], None
        oldest = now - days * 86400
        while True:
            params = {"series_ticker": series, "status": "settled", "limit": 100,
                      "with_nested_markets": "true"}
            if cursor:
                params["cursor"] = cursor
            data = self._get(f"{KALSHI}/events", params)
            evs = data.get("events") or []
            for e in evs:
                close = max((_ts(m.get("close_time")) or 0) for m in e.get("markets") or [{}])
                if close and close >= oldest:
                    out.append(e)
            cursor = data.get("cursor")
            last_close = min(((_ts(m.get("close_time")) or now) for e in evs
                              for m in e.get("markets") or []), default=0)
            if not cursor or not evs or last_close < oldest:
                return out

    def spot(self, product: str, start: float, end: float) -> list[list]:
        rows, t = [], start
        while t < end:
            t2 = min(end, t + 300 * 60)
            iso = lambda x: datetime.fromtimestamp(x, timezone.utc).isoformat()  # noqa: E731
            rows.extend(self._get(f"{COINBASE}/products/{product}/candles",
                                  {"granularity": 60, "start": iso(t), "end": iso(t2)}) or [])
            t = t2
        return rows

    def candles(self, series: str, ticker: str, close: float) -> list[dict]:
        data = self._get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks",
                         {"start_ts": int(close - 3660), "end_ts": int(close), "period_interval": 1})
        return data.get("candlesticks") or []


def select_strikes(markets: list[dict], spot0: float, band: float = 0.015, cap: int = 12) -> list[dict]:
    """Strikes near SPOT AT THE DECISION START — never by volume or result."""
    rows = []
    for m in markets:
        k = m.get("floor_strike")
        if m.get("strike_type") != "greater" or k is None or m.get("result") not in ("yes", "no"):
            continue
        d = abs(math.log(float(k) / spot0))
        if d <= band:
            rows.append((d, m))
    rows.sort(key=lambda r: r[0])
    return [m for _, m in rows[:cap]]


def fetch(series: str, days: float, fetcher: Optional[Fetcher] = None, now: Optional[float] = None,
          cache_dir: str = CACHE) -> str:
    f = fetcher or Fetcher()
    now = now or time.time()
    os.makedirs(cache_dir, exist_ok=True)
    events = f.events(series, days, now)
    closes = [max(_ts(m["close_time"]) for m in e["markets"]) for e in events if e.get("markets")]
    if not closes:
        raise SystemExit("no settled events in range")
    spot_rows = f.spot(SPOT_PRODUCT[series], min(closes) - 3 * 3600, max(closes) + 120)
    spot = SpotSeries(spot_rows)
    dump = {"series": series, "fetched": now, "spot": spot_rows, "events": []}
    for i, e in enumerate(events):
        close = max(_ts(m["close_time"]) for m in e["markets"])
        s0 = spot.at(close - 3600)
        if s0 is None:
            continue
        rows = []
        for m in select_strikes(e["markets"], s0):
            try:
                cs = f.candles(series, m["ticker"], close)
            except Exception as exc:  # noqa: BLE001 — a missing ladder rung is a missing row
                log.warning("candles failed %s: %s", m["ticker"], exc)
                continue
            rows.append({"ticker": m["ticker"], "strike": float(m["floor_strike"]), "result": m["result"],
                         "settle_value": m.get("expiration_value"), "candles": cs})
        dump["events"].append({"event": e["event_ticker"], "close": close, "markets": rows})
        if i % 25 == 0:
            log.info("%s: %d/%d events", series, i + 1, len(events))
    path = os.path.join(cache_dir, f"{series}_{int(now)}.json")
    with open(path, "w") as fh:
        json.dump(dump, fh)
    return path


# ------------------------------------------------------------------ analyse
def _quote(candles: list[dict], ts: float) -> tuple[Optional[float], Optional[float]]:
    """Book at ts: the latest candle that ENDED at or before ts."""
    best = None
    for c in candles:
        end = float(c.get("end_period_ts") or 0)
        if end <= ts and (best is None or end > float(best["end_period_ts"])):
            best = c
    if best is None:
        return None, None

    def px(side):
        try:
            v = float((best.get(side) or {}).get("close_dollars"))
        except (TypeError, ValueError):
            return None
        return v if 0.0 < v < 1.0 else None
    return px("yes_bid"), px("yes_ask")


def _clustered(vals: list[float], groups: list[str]) -> tuple[Optional[float], Optional[float]]:
    if not vals:
        return None, None
    m = statistics.mean(vals)
    by: dict[str, float] = defaultdict(float)
    for v, g in zip(vals, groups):
        by[g] += v - m
    return m, (math.sqrt(sum(x * x for x in by.values())) / len(vals)) if len(vals) > 1 else None


def analyse(dump: dict, margins=(0.0, 0.02, 0.05), taus=TAUS) -> dict:
    spot = SpotSeries(dump["spot"])
    out: dict = {"series": dump["series"], "events": len(dump["events"]), "by_tau": {}}
    for tau in taus:
        brier_m, brier_b, gx, gy, groups = [], [], [], [], []
        trades = {m: ([], []) for m in margins}
        for e in dump["events"]:
            t = e["close"] - tau * 60
            s, sig = spot.at(t), spot.sigma(t)
            if s is None or sig is None:
                continue
            for mk in e["markets"]:
                bid, ask = _quote(mk["candles"], t)
                p = model_prob(s, mk["strike"], sig, tau)
                if bid is None or ask is None or p is None or ask <= bid:
                    continue
                y = 1.0 if mk["result"] == "yes" else 0.0
                mid = (bid + ask) / 2
                if 0.03 < mid < 0.97:
                    brier_m.append((p - y) ** 2)
                    brier_b.append((mid - y) ** 2)
                    gx.append(p - mid)
                    gy.append(y - mid)
                    groups.append(e["event"])
                for margin in margins:
                    vals, gs = trades[margin]
                    if p - ask - taker_fee(ask) > margin:
                        vals.append(y - ask - taker_fee(ask))
                        gs.append(e["event"])
                    no_price = 1.0 - bid
                    if (1.0 - p) - no_price - taker_fee(no_price) > margin:
                        vals.append((1.0 - y) - no_price - taker_fee(no_price))
                        gs.append(e["event"])
        row: dict = {"n": len(brier_m)}
        if brier_m:
            row["brier_model"] = statistics.mean(brier_m)
            row["brier_book"] = statistics.mean(brier_b)
            diff = [a - b for a, b in zip(brier_m, brier_b)]
            row["brier_diff"], row["brier_diff_se"] = _clustered(diff, groups)
            sxx = sum(x * x for x in gx)
            if sxx > 0:
                beta = sum(x * y for x, y in zip(gx, gy)) / sxx
                resid = [y - beta * x for x, y in zip(gx, gy)]
                by: dict[str, float] = defaultdict(float)
                for x, r_, g in zip(gx, resid, groups):
                    by[g] += x * r_
                row["beta"] = beta
                row["beta_se"] = math.sqrt(sum(v * v for v in by.values())) / sxx
        row["taker"] = {}
        for margin, (vals, gs) in trades.items():
            m, se = _clustered(vals, gs)
            row["taker"][margin] = {"n": len(vals), "events": len(set(gs)), "mean": m, "se": se}
        out["by_tau"][tau] = row
    return out


def format_analysis(a: dict) -> str:
    lines = [f"{a['series']}: {a['events']} settled hourly events",
             "tau  rows   Brier model  Brier book   diff ± se          beta ± se       | taker net per contract "
             "(margin: n, mean ± se)"]
    for tau, r in a["by_tau"].items():
        if not r.get("n"):
            lines.append(f"{tau:3d}  0")
            continue
        se = r.get("brier_diff_se") or 0.0
        beta = f"{r['beta']:+.3f} ± {r['beta_se']:.3f}" if "beta" in r else "—"
        tk = "  ".join(
            f"{m:.2f}: {v['n']} {v['mean'] * 100:+.2f}±{(v['se'] or 0) * 100:.2f}pt" if v["n"] else f"{m:.2f}: 0"
            for m, v in r["taker"].items())
        lines.append(f"{tau:3d} {r['n']:5d}   {r['brier_model']:.4f}       {r['brier_book']:.4f}    "
                     f"{r['brier_diff']:+.4f} ± {se:.4f}   {beta:16s} | {tk}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--series", default="KXBTCD", choices=sorted(SPOT_PRODUCT))
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--analyse", action="store_true", help="analyse the newest cached fetch for the series")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.analyse:
        files = sorted(f for f in os.listdir(CACHE) if f.startswith(args.series + "_"))
        if not files:
            raise SystemExit("nothing cached; run without --analyse first")
        with open(os.path.join(CACHE, files[-1])) as fh:
            print(format_analysis(analyse(json.load(fh))))
        return
    print(fetch(args.series, args.days))


if __name__ == "__main__":
    main()
