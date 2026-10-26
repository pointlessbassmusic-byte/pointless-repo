"""Kalshi's daily AAA gas ladder (KXAAAGASD) against yesterday's print: does
the book misprice a one-day change in a number every trader already knows?
DATA ONLY — public reads, no orders.

The ladder for date t ("AAA US regular average strictly greater than K on
t") trades from 12:00Z on t-1 until 03:59Z on t (midnight ET), before AAA
publishes t's figure, so the t-1 figure is public at every decision. Each
market's `expiration_value` is the settled AAA number, so the series' own
history comes from the settlements.

PRE-REGISTRATION: docs/GAS_DAILY_PREREG_2026-10-26.md (repo root), committed
before any price was scored. Primary model, fixed there:
  mu_t    = mean of the 5 latest daily changes known at the decision
  sigma_t = sd of (delta_s - mu_s) over the 60 latest prior days, floor
            $0.002, >= 20 residuals or the event is skipped
  P(V_t > K) = 1 - Phi((K - V_{t-1} - mu_t) / sigma_t)
Decision: the hourly candle ending nearest close - 2 h (within 1 h).
Rows: two-sided, ask in [0.03, 0.97]. Taker: YES at the ask when
p - ask >= 0.05, NO at 1 - bid when (1 - p) - (1 - bid) >= 0.05, one
contract, net of 0.07 p (1 - p), paid at the market's result.
PASS: >= 100 trades, mean > 0 with event-clustered t >= 2, same sign in
each chronological half, and model Brier - book Brier CI upper <= +0.002.

Run: `python -m src.gas_daily_study` (fetch, cached under data/gas_daily/)
then `--analyse`.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
from datetime import date, datetime, timedelta
from typing import Optional

from .crypto_hourly_study import KALSHI, Fetcher, _clustered, _ts, phi, taker_fee
from .index_hourly_study import normalise_candle

log = logging.getLogger(__name__)

SERIES = "KXAAAGASD"
CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "gas_daily")
LEAD_S = 2 * 3600
TOL_S = 3600
MARGIN = 0.05
DRIFT_N = 5
SIGMA_N = 60
SIGMA_MIN_N = 20
SIGMA_FLOOR = 0.002


def event_date(event_ticker: str) -> Optional[date]:
    try:
        return datetime.strptime(event_ticker.split("-")[1], "%y%b%d").date()
    except (IndexError, ValueError):
        return None


# ------------------------------------------------------------------ model
def history(values: dict[date, float]) -> dict[date, tuple[float, float, float]]:
    """{t: (V_{t-1}, mu_t, sigma_t)} for every t the primary model can price.
    Uses only values dated before t."""
    days = sorted(values)
    delta = {d: values[d] - values[d - timedelta(days=1)]
             for d in days if d - timedelta(days=1) in values}
    mu: dict[date, float] = {}
    out = {}
    for t in days:
        prev = t - timedelta(days=1)
        if prev not in values:
            continue
        known = [d for d in sorted(delta) if d <= prev]      # changes published by t-1
        if len(known) < DRIFT_N:
            continue
        mu[t] = statistics.mean(delta[d] for d in known[-DRIFT_N:])
        resid = [delta[d] - mu[d] for d in known if d in mu][-SIGMA_N:]
        if len(resid) < SIGMA_MIN_N:
            continue
        out[t] = (values[prev], mu[t], max(statistics.pstdev(resid), SIGMA_FLOOR))
    return out


def model_prob(strike: float, prev: float, mu: float, sigma: float) -> float:
    return 1.0 - phi((strike - prev - mu) / sigma)


# ------------------------------------------------------------------ fetch
def _quote(candles: list[dict], ts: float) -> tuple[Optional[float], Optional[float]]:
    best = None
    for c in candles:
        end = c.get("end_period_ts")
        if end is None or abs(end - ts) > TOL_S:
            continue
        if best is None or abs(end - ts) < abs(best["end_period_ts"] - ts):
            best = c
    if best is None:
        return None, None

    def px(side):
        try:
            return float(best[side]["close_dollars"])
        except (KeyError, TypeError, ValueError):
            return None
    return px("yes_bid"), px("yes_ask")


def fetch(fetcher: Optional[Fetcher] = None, cache_dir: str = CACHE) -> list[dict]:
    f = fetcher or Fetcher()
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "events.jsonl")
    done = {}
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                e = json.loads(line)
                done[e["event_ticker"]] = e
    by: dict[str, dict] = {}
    for url, extra, hist in ((f"{KALSHI}/markets", {"status": "settled"}, False),
                             (f"{KALSHI}/historical/markets", {}, True)):
        cursor = None
        while True:
            params = {"series_ticker": SERIES, "limit": 1000, **extra}
            if cursor:
                params["cursor"] = cursor
            data = f._get(url, params) or {}
            for m in data.get("markets") or []:
                ev = m.get("event_ticker", "")
                if ev.startswith(SERIES + "-") and m.get("result") in ("yes", "no"):
                    e = by.setdefault(ev, {"event_ticker": ev, "historical": hist, "markets": {}})
                    e["markets"].setdefault(m["ticker"], m)
            cursor = data.get("cursor")
            if not cursor or not data.get("markets"):
                break
    with open(path, "a") as out:
        for ev, e in sorted(by.items()):
            if ev in done:
                continue
            ms = list(e["markets"].values())
            close = _ts(ms[0].get("close_time"))
            if close is None:
                continue
            span = {"start_ts": int(close - 8 * 3600), "end_ts": int(close), "period_interval": 60}
            candles: dict[str, list] = {}
            if not e["historical"]:
                data = f._get(f"{KALSHI}/markets/candlesticks",
                              {"market_tickers": ",".join(m["ticker"] for m in ms), **span}) or {}
                candles = {x.get("market_ticker"): x.get("candlesticks") or [] for x in data.get("markets") or []}
            for m in ms:
                if not candles.get(m["ticker"]):
                    try:
                        data = f._get(f"{KALSHI}/historical/markets/{m['ticker']}/candlesticks", span) or {}
                    except Exception as exc:  # noqa: BLE001 — a strike that never traded has no candles
                        log.debug("no candles for %s: %s", m["ticker"], exc)
                        data = {}
                    candles[m["ticker"]] = data.get("candlesticks") or []
            row = {"event_ticker": ev, "close": close,
                   "markets": [{"ticker": m["ticker"], "strike": m.get("floor_strike"),
                                "type": m.get("strike_type"), "result": m["result"],
                                "value": m.get("expiration_value"),
                                "candles": [normalise_candle(c) for c in candles.get(m["ticker"], [])]}
                               for m in ms]}
            out.write(json.dumps(row) + "\n")
            done[ev] = row
            log.info("fetched %s (%d strikes)", ev, len(ms))
    return sorted(done.values(), key=lambda e: e["event_ticker"])


# ------------------------------------------------------------------ score
def rows(events: list[dict], drift: bool = True) -> list[tuple]:
    """(event, date, y, p, bid, ask) per scorable strike."""
    values = {}
    for e in events:
        d = event_date(e["event_ticker"])
        v = next((m["value"] for m in e["markets"] if m.get("value") not in (None, "")), None)
        if d and v is not None:
            values[d] = float(v)
    hist = history(values)
    out = []
    for e in events:
        d = event_date(e["event_ticker"])
        if d not in hist:
            continue
        prev, mu, sigma = hist[d]
        for m in e["markets"]:
            if m.get("type") != "greater" or m.get("strike") is None:
                continue
            bid, ask = _quote(m["candles"], e["close"] - LEAD_S)
            if bid is None or ask is None or not bid < ask or not 0.03 <= ask <= 0.97:
                continue
            p = model_prob(float(m["strike"]), prev, mu if drift else 0.0, sigma)
            out.append((e["event_ticker"], d, 1.0 if m["result"] == "yes" else 0.0, p, bid, ask))
    return out


def trades(rs: list[tuple], margin: float = MARGIN) -> list[tuple]:
    """(event, date, net) for every taker trade the rule takes."""
    out = []
    for ev, d, y, p, bid, ask in rs:
        if p - ask >= margin:
            out.append((ev, d, y - ask - taker_fee(ask)))
        no = 1.0 - bid
        if (1.0 - p) - no >= margin:
            out.append((ev, d, (1.0 - y) - no - taker_fee(no)))
    return out


def score(rs: list[tuple]) -> dict:
    g = [r[0] for r in rs]
    diffs = [(r[3] - r[2]) ** 2 - ((r[4] + r[5]) / 2 - r[2]) ** 2 for r in rs]
    bd, bse = _clustered(diffs, g)
    tr = trades(rs)
    tm, tse = _clustered([t[2] for t in tr], [t[0] for t in tr])
    half = sorted({t[1] for t in tr})
    cut = half[len(half) // 2] if half else None
    halves = []
    for keep in (lambda d: d < cut, lambda d: d >= cut):
        sub = [t for t in tr if cut and keep(t[1])]
        halves.append(_clustered([t[2] for t in sub], [t[0] for t in sub])[0])
    return {"rows": len(rs), "events": len(set(g)),
            "brier_model": statistics.mean((r[3] - r[2]) ** 2 for r in rs) if rs else None,
            "brier_book": statistics.mean(((r[4] + r[5]) / 2 - r[2]) ** 2 for r in rs) if rs else None,
            "brier_diff": bd, "brier_diff_se": bse,
            "trades": len(tr), "trade_events": len({t[0] for t in tr}), "mean": tm, "se": tse,
            "halves": halves}


def verdict(s: dict) -> str:
    if s["trades"] < 100:
        return "INCONCLUSIVE (< 100 trades)"
    t = s["mean"] / s["se"] if s["se"] else 0.0
    ok = (s["mean"] > 0 and t >= 2 and all(h is not None and h > 0 for h in s["halves"])
          and s["brier_diff"] is not None and s["brier_diff_se"] is not None
          and s["brier_diff"] + 1.96 * s["brier_diff_se"] <= 0.002)
    return "PASS" if ok else "FAIL"


def format_analysis(events: list[dict]) -> str:
    lines = [f"KXAAAGASD daily ladder vs yesterday's AAA print, decision at close - 2 h "
             f"({len(events)} events fetched)"]
    for name, drift in (("primary (5-day drift)", True), ("secondary (zero drift)", False)):
        s = score(rows(events, drift))
        if not s["rows"]:
            lines.append(f"  {name}: no scorable rows")
            continue
        t = s["mean"] / s["se"] if s["mean"] is not None and s["se"] else float("nan")
        lines += [f"  {name}: {s['rows']} rows in {s['events']} events",
                  f"    Brier model {s['brier_model']:.4f}  book {s['brier_book']:.4f}  "
                  f"diff {s['brier_diff']:+.4f} +- {1.96 * (s['brier_diff_se'] or 0):.4f}",
                  f"    taker (edge >= {MARGIN:.2f}): {s['trades']} trades in {s['trade_events']} events, "
                  f"mean net {s['mean'] if s['mean'] is not None else float('nan'):+.4f}/contract (t {t:.2f}); "
                  f"halves {', '.join('n/a' if h is None else f'{h:+.4f}' for h in s['halves'])}"]
        if drift:
            lines.append(f"    VERDICT (pre-registered): {verdict(s)}")
    return "\n".join(lines)


def load(cache_dir: str = CACHE) -> list[dict]:
    path = os.path.join(cache_dir, "events.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return sorted((json.loads(x) for x in fh), key=lambda e: e["event_ticker"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--analyse", action="store_true", help="score the cached fetch (offline)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    events = load() if a.analyse else fetch()
    print(format_analysis(events))


if __name__ == "__main__":
    main()
