"""Kalshi hourly stock-index markets against the index itself: is the book
a step behind the price it settles on?  DATA ONLY — public reads, no orders.

KXINXU (S&P 500) and KXNASDAQ100U (Nasdaq-100) list, for every hour from
11:00 to 16:00 ET, a ladder of "index >= K at H:00" binaries settled on the
index value at that time (the official close for 16:00). The index is the
deepest-priced object in finance (futures, options, ETFs) and the Kalshi
crowd is thin next to it: on paper the reference-price property the Fed
rule has (repo CLAUDE.md, "What resolved data says about edges"). Crypto
failed the same test twice (Deribit vs Polymarket digitals; Kalshi hourly
BTC/ETH vs spot, 2026-10-14). Never measured for indices before 2026-10-15.

PRE-REGISTRATION (written 2026-10-15, committed before the first fetch):

* universe: every settled KXINXU and KXNASDAQ100U hourly event whose
  decision time has a Yahoo hourly bar, from each series' start to
  2026-10-14;
* index: Yahoo 60-minute bars, stamped by their OPEN time by Yahoo and
  re-keyed here by close (the Deribit lesson); bars open on the half hour,
  so the bar closing at H-30 gives the index 30 minutes before every
  hourly settlement, and H-90 for 12:00-16:00;
* strikes: the `cap` nearest the index AT THE DECISION TIME within `band`
  log-distance — never by volume or result;
* book: Kalshi 1-minute candles (yes_bid / yes_ask close), latest candle
  ENDED at or before the decision time;
* model: p = Phi(ln(S/K) / (sigma * sqrt(tau))), driftless, sigma per
  minute from the squared intraday returns of the previous 35 hourly bars
  (about five sessions; overnight gaps excluded) — no fitted parameter.
  Secondary, reported but not eligible to pass: sigma from the previous
  session's VIX close, (VIX / 100) / sqrt(252 * 390);
* scoring: Brier of the model and of the book mid against the settled
  result on two-sided books with mid inside 0.03-0.97; the regression of
  (outcome - mid) on (model - mid); the taker rule "buy YES at the ask
  when p - ask - fee > margin" (and the NO mirror at 1 - bid), paid at
  settlement net of Kalshi's quadratic taker fee 0.07 p (1 - p). Errors
  clustered by event.
* THE BAR, primary = tau 30, both series pooled: the index is a leading
  reference for this book only if Brier(model) - Brier(book) < 0 with
  t <= -2 AND the taker rule at margin 0.02 has >= 100 trades in >= 40
  events with mean net > 0 and t >= 2. Anything else: not a member (the
  seventh measured non-member). tau 90, the VIX model, per-series and
  per-half splits are reported for context and cannot pass the test.

Run: `python -m src.index_hourly_study` (fetch, cached under
data/index_hourly/) then `--analyse`.
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
from typing import Optional

from .crypto_hourly_study import KALSHI, Fetcher, _clustered, _quote, _ts, phi, taker_fee

log = logging.getLogger(__name__)

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart"
INDEX = {"KXINXU": "^GSPC", "KXNASDAQ100U": "^NDX"}
TAUS = (90, 30)
PRIMARY_TAU = 30
BAR_S = 3600
LOOKBACK_BARS = 35
CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "index_hourly")


def model_prob(spot: float, strike: float, sigma_min: float, tau_min: float) -> Optional[float]:
    """P(index at settlement >= strike), driftless lognormal, point settlement."""
    if spot <= 0 or strike <= 0 or sigma_min <= 0 or tau_min <= 0:
        return None
    return phi(math.log(spot / strike) / (sigma_min * math.sqrt(tau_min)))


def vix_sigma(vix: float) -> float:
    """VIX is annualised; spread it over trading minutes only."""
    return vix / 100.0 / math.sqrt(252 * 390)


class IndexBars:
    """Hourly bars keyed by CLOSE (Yahoo stamps open; the last bar of a
    session is a half hour, 15:30-16:00, and the closing print arrives as
    a zero-length bar at 16:00, which is dropped: it is the settlement)."""

    def __init__(self, timestamps: list, opens: list, closes: list, bar_s: int = BAR_S):
        stamps = sorted(float(t) for t in timestamps if t is not None)
        rows = []
        for t, o, c in zip(timestamps, opens, closes):
            if t is None or o is None or c is None:
                continue
            t = float(t)
            if t % 3600 == 0:
                continue        # sessions open on the half hour: a bar on the hour is the closing print
            i = bisect_right(stamps, t)
            nxt = stamps[i] if i < len(stamps) else None
            end = min(t + bar_s, nxt) if nxt is not None else t + bar_s   # 15:30 bar ends at 16:00
            rows.append((end, t, float(o), float(c)))
        rows.sort()
        self.end = [r[0] for r in rows]
        self.start = [r[1] for r in rows]
        self.open = [r[2] for r in rows]
        self.close = [r[3] for r in rows]

    def at(self, ts: float, stale_s: float = 300.0) -> Optional[float]:
        i = bisect_right(self.end, ts) - 1
        if i < 0 or ts - self.end[i] > stale_s:
            return None
        return self.close[i]

    def sigma(self, ts: float, lookback: int = LOOKBACK_BARS) -> Optional[float]:
        """Per-minute vol from intraday returns of the bars closed by ts."""
        i = bisect_right(self.end, ts) - 1
        if i < lookback:
            return None
        ss, mins = 0.0, 0.0
        for j in range(i - lookback + 1, i + 1):
            same_session = j > 0 and self.start[j] - self.end[j - 1] < 60
            base = self.close[j - 1] if same_session else self.open[j]
            span = (self.end[j] - (self.end[j - 1] if same_session else self.start[j])) / 60.0
            if base <= 0 or self.close[j] <= 0 or span <= 0:
                continue
            ss += math.log(self.close[j] / base) ** 2
            mins += span
        return math.sqrt(ss / mins) if mins > 0 and ss > 0 else None


class DailyCloses:
    def __init__(self, timestamps: list, closes: list):
        rows = sorted((float(t), float(c)) for t, c in zip(timestamps, closes) if t is not None and c is not None)
        self.t = [r[0] for r in rows]
        self.c = [r[1] for r in rows]

    def previous(self, ts: float) -> Optional[float]:
        """The last daily close from a session that STARTED a day or more before ts."""
        i = bisect_right(self.t, ts - 86400) - 1
        return self.c[i] if i >= 0 else None


# ------------------------------------------------------------------ fetch
class IndexFetcher(Fetcher):
    def yahoo(self, symbol: str, interval: str, rng: str) -> dict:
        r = self.s.get(f"{YAHOO}/{symbol}", params={"interval": interval, "range": rng}, timeout=30,
                       headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        return {"t": res["timestamp"], "open": q["open"], "close": q["close"]}

    def batch_candles(self, tickers: list[str], close: float, span_s: int = 6000) -> dict[str, list]:
        data = self._get(f"{KALSHI}/markets/candlesticks",
                         {"market_tickers": ",".join(tickers), "start_ts": int(close - span_s),
                          "end_ts": int(close), "period_interval": 1}) or {}
        return {m.get("market_ticker"): m.get("candlesticks") or [] for m in data.get("markets") or []}


def select_strikes(markets: list[dict], spot: float, band: float = 0.006, cap: int = 8) -> list[dict]:
    rows = []
    for m in markets:
        k = m.get("floor_strike")
        if m.get("strike_type") not in ("greater", "greater_or_equal") or k is None \
                or m.get("result") not in ("yes", "no"):
            continue
        d = abs(math.log(float(k) / spot))
        if d <= band:
            rows.append((d, m))
    rows.sort(key=lambda r: r[0])
    return [m for _, m in rows[:cap]]


def fetch(series: str, days: float = 400, fetcher: Optional[IndexFetcher] = None, now: Optional[float] = None,
          cache_dir: str = CACHE) -> str:
    f = fetcher or IndexFetcher()
    now = now or time.time()
    os.makedirs(cache_dir, exist_ok=True)
    bars_raw = f.yahoo(INDEX[series], "60m", "730d")
    vix_raw = f.yahoo("^VIX", "1d", "2y")
    bars = IndexBars(bars_raw["t"], bars_raw["open"], bars_raw["close"])
    events = f.events(series, days, now)
    dump = {"series": series, "fetched": now, "bars": bars_raw, "vix": vix_raw, "events": []}
    for i, e in enumerate(events):
        if not e.get("markets"):
            continue
        close = max(_ts(m["close_time"]) or 0 for m in e["markets"])
        s0 = bars.at(close - PRIMARY_TAU * 60)
        if s0 is None:
            continue
        chosen = select_strikes(e["markets"], s0)
        if not chosen:
            continue
        try:
            candles = f.batch_candles([m["ticker"] for m in chosen], close)
        except Exception as exc:  # noqa: BLE001 — a missing event is a missing row
            log.warning("candles failed %s: %s", e["event_ticker"], exc)
            continue
        dump["events"].append({"event": e["event_ticker"], "close": close, "markets": [
            {"ticker": m["ticker"], "strike": float(m["floor_strike"]), "result": m["result"],
             "settle_value": m.get("expiration_value"), "candles": candles.get(m["ticker"]) or []}
            for m in chosen]})
        if i % 100 == 0:
            log.info("%s: %d/%d events", series, i + 1, len(events))
    path = os.path.join(cache_dir, f"{series}_{int(now)}.json")
    with open(path, "w") as fh:
        json.dump(dump, fh)
    return path


# ------------------------------------------------------------------ analyse
def _rows(dumps: list[dict], tau: int, model: str = "realised"):
    """(event, y, p, bid, ask, close) per priced strike at tau."""
    for d in dumps:
        bars = IndexBars(d["bars"]["t"], d["bars"]["open"], d["bars"]["close"])
        vix = DailyCloses(d["vix"]["t"], d["vix"]["close"])
        for e in d["events"]:
            t = e["close"] - tau * 60
            s = bars.at(t)
            if s is None:
                continue
            if model == "vix":
                v = vix.previous(t)
                sig = vix_sigma(v) if v else None
            else:
                sig = bars.sigma(t)
            if sig is None:
                continue
            for mk in e["markets"]:
                bid, ask = _quote(mk["candles"], t)
                p = model_prob(s, mk["strike"], sig, tau)
                if bid is None or ask is None or p is None or ask <= bid:
                    continue
                yield e["event"], (1.0 if mk["result"] == "yes" else 0.0), p, bid, ask, e["close"]


def score(rows: list[tuple], margins=(0.0, 0.02, 0.05)) -> dict:
    brier_m, brier_b, gx, gy, groups = [], [], [], [], []
    trades = {m: ([], []) for m in margins}
    for ev, y, p, bid, ask, _ in rows:
        mid = (bid + ask) / 2
        if 0.03 < mid < 0.97:
            brier_m.append((p - y) ** 2)
            brier_b.append((mid - y) ** 2)
            gx.append(p - mid)
            gy.append(y - mid)
            groups.append(ev)
        for margin in margins:
            vals, gs = trades[margin]
            if p - ask - taker_fee(ask) > margin:
                vals.append(y - ask - taker_fee(ask))
                gs.append(ev)
            no_price = 1.0 - bid
            if (1.0 - p) - no_price - taker_fee(no_price) > margin:
                vals.append((1.0 - y) - no_price - taker_fee(no_price))
                gs.append(ev)
    row: dict = {"n": len(brier_m), "events": len(set(groups))}
    if brier_m:
        row["brier_model"] = statistics.mean(brier_m)
        row["brier_book"] = statistics.mean(brier_b)
        row["brier_diff"], row["brier_diff_se"] = _clustered([a - b for a, b in zip(brier_m, brier_b)], groups)
        sxx = sum(x * x for x in gx)
        if sxx > 0:
            beta = sum(x * y for x, y in zip(gx, gy)) / sxx
            by: dict[str, float] = defaultdict(float)
            for x, y_, g in zip(gx, gy, groups):
                by[g] += x * (y_ - beta * x)
            row["beta"], row["beta_se"] = beta, math.sqrt(sum(v * v for v in by.values())) / sxx
    row["taker"] = {}
    for margin, (vals, gs) in trades.items():
        m, se = _clustered(vals, gs)
        row["taker"][margin] = {"n": len(vals), "events": len(set(gs)), "mean": m, "se": se}
    return row


def verdict(primary: dict) -> str:
    d, se = primary.get("brier_diff"), primary.get("brier_diff_se")
    tk = primary["taker"].get(0.02) or {}
    brier_ok = d is not None and se and d / se <= -2
    taker_ok = (tk.get("n", 0) >= 100 and tk.get("events", 0) >= 40 and tk.get("mean") is not None
                and tk.get("se") and tk["mean"] > 0 and tk["mean"] / tk["se"] >= 2)
    return "MEMBER (passes the pre-registered bar)" if brier_ok and taker_ok else "not a member"


def analyse(dumps: list[dict]) -> dict:
    out: dict = {"series": [d["series"] for d in dumps], "events": sum(len(d["events"]) for d in dumps), "rows": {}}
    for model in ("realised", "vix"):
        for tau in TAUS:
            rows = list(_rows(dumps, tau, model))
            out["rows"][f"{model} tau {tau}"] = score(rows)
            if model == "realised" and tau == PRIMARY_TAU:
                closes = sorted(r[5] for r in rows)
                cut = closes[len(closes) // 2] if closes else 0
                out["rows"]["  first half"] = score([r for r in rows if r[5] < cut])
                out["rows"]["  second half"] = score([r for r in rows if r[5] >= cut])
                for s in out["series"]:
                    out["rows"][f"  {s} only"] = score([r for r in rows if r[0].startswith(s + "-")])
    out["verdict"] = verdict(out["rows"][f"realised tau {PRIMARY_TAU}"])
    return out


def format_analysis(a: dict) -> str:
    lines = [f"{'+'.join(a['series'])}: {a['events']} settled hourly events",
             f"{'arm':24s} rows  evts  Brier model  book     diff ± se          beta ± se       | "
             "taker net per contract (margin: n, mean ± se)"]
    for name, r in a["rows"].items():
        if not r.get("n"):
            lines.append(f"{name:24s} 0")
            continue
        se = r.get("brier_diff_se") or 0.0
        beta = f"{r['beta']:+.3f} ± {r['beta_se']:.3f}" if "beta" in r else "—"
        tk = "  ".join(
            f"{m:.2f}: {v['n']} {v['mean'] * 100:+.2f}±{(v['se'] or 0) * 100:.2f}pt" if v["n"] else f"{m:.2f}: 0"
            for m, v in r["taker"].items())
        lines.append(f"{name:24s} {r['n']:5d} {r['events']:4d}  {r['brier_model']:.4f}  {r['brier_book']:.4f}  "
                     f"{r['brier_diff']:+.4f} ± {se:.4f}   {beta:16s} | {tk}")
    lines.append(f"verdict (tau {PRIMARY_TAU}, pre-registered): {a['verdict']}")
    return "\n".join(lines)


def _latest(series: str) -> Optional[str]:
    if not os.path.isdir(CACHE):
        return None
    files = sorted(f for f in os.listdir(CACHE) if f.startswith(series + "_"))
    return os.path.join(CACHE, files[-1]) if files else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--series", nargs="+", default=sorted(INDEX), choices=sorted(INDEX))
    ap.add_argument("--days", type=float, default=400)
    ap.add_argument("--analyse", action="store_true", help="analyse the newest cached fetch of each series")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.analyse:
        paths = [p for p in (_latest(s) for s in args.series) if p]
        if not paths:
            raise SystemExit("nothing cached; run without --analyse first")
        dumps = []
        for p in paths:
            with open(p) as fh:
                dumps.append(json.load(fh))
        print(format_analysis(analyse(dumps)))
        return
    for s in args.series:
        print(fetch(s, args.days))


if __name__ == "__main__":
    main()
