"""Forward recorder for the hourly crypto study's final minutes. DATA ONLY.

The historical study (`crypto_hourly_study.py`) can only see spot as
1-minute closes, up to a minute stale, while the Kalshi book reacts to the
live index; inside the last few minutes that makes the comparison unfair to
the model. This recorder closes the gap: from `lead_min` before each hour
it samples, every `interval` seconds,

* the Kalshi order book (best YES bid / ask and sizes) for the strikes of
  the event closing at the hour that sit within `band` of spot, and
* live spot from Kraken (public Ticker) and Coinbase (public ticker),
  the two largest US constituents of the CF Benchmarks index the market
  settles on,

and afterwards stores each market's result. Public reads only: no keys,
no orders. `--report` scores the recorded rows with the study's model and
taker rule, spot now seconds old instead of a minute.

Run: `python -m src.crypto_hourly_recorder --series KXBTCD --hours 6`
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import sqlite3
import statistics
import time
from typing import Optional

from .crypto_hourly_study import KALSHI, _clustered, _ts, model_prob, taker_fee

log = logging.getLogger(__name__)

KRAKEN = "https://api.kraken.com/0/public/Ticker"
COINBASE_TICKER = "https://api.exchange.coinbase.com/products/{}/ticker"
PAIRS = {"KXBTCD": ("XBTUSD", "BTC-USD"), "KXETHD": ("ETHUSD", "ETH-USD")}
DB = os.path.join(os.path.dirname(__file__), "..", "data", "crypto_hourly", "recorder.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (ticker TEXT PRIMARY KEY, event TEXT, series TEXT,
    strike REAL, close_ts REAL, result TEXT);
CREATE TABLE IF NOT EXISTS quotes (ts REAL, ticker TEXT, bid REAL, ask REAL, bid_qty REAL, ask_qty REAL);
CREATE INDEX IF NOT EXISTS quotes_t ON quotes(ticker, ts);
CREATE TABLE IF NOT EXISTS spot (ts REAL, series TEXT, venue TEXT, price REAL);
CREATE INDEX IF NOT EXISTS spot_t ON spot(series, ts);
"""


def kalshi_top(orderbook: dict) -> tuple[Optional[float], Optional[float], float, float]:
    """Kalshi books are two bid ladders: a NO bid at q is a YES ask at 1 - q."""
    ob = orderbook.get("orderbook_fp") or orderbook.get("orderbook") or {}
    yes = [(float(p), float(s)) for p, s in (ob.get("yes_dollars") or [])]
    no = [(float(p), float(s)) for p, s in (ob.get("no_dollars") or [])]
    bid = max(yes) if yes else None
    nb = max(no) if no else None
    return (bid[0] if bid else None, round(1.0 - nb[0], 4) if nb else None,
            bid[1] if bid else 0.0, nb[1] if nb else 0.0)


def kraken_mid(payload: dict) -> Optional[float]:
    if payload.get("error"):
        return None
    for v in (payload.get("result") or {}).values():
        try:
            return (float(v["b"][0]) + float(v["a"][0])) / 2
        except (KeyError, IndexError, TypeError, ValueError):
            return None
    return None


def coinbase_mid(payload: dict) -> Optional[float]:
    try:
        return (float(payload["bid"]) + float(payload["ask"])) / 2
    except (KeyError, TypeError, ValueError):
        return None


class Recorder:
    def __init__(self, series: str = "KXBTCD", db: str = DB, session=None, clock=time.time, sleep=time.sleep):
        os.makedirs(os.path.dirname(db), exist_ok=True)
        self.conn = sqlite3.connect(db)
        self.conn.executescript(SCHEMA)
        self.series = series
        if session is None:
            from .http_util import retrying_session
            session = retrying_session()
            session.headers["User-Agent"] = "kalshi-engine-recorder/1.0"
        self.s = session
        self.clock, self.sleep = clock, sleep

    def _get(self, url, params=None):
        r = self.s.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def spot(self) -> dict[str, Optional[float]]:
        kpair, cprod = PAIRS[self.series]
        out = {}
        for venue, fn in (("kraken", lambda: kraken_mid(self._get(KRAKEN, {"pair": kpair}))),
                          ("coinbase", lambda: coinbase_mid(self._get(COINBASE_TICKER.format(cprod))))):
            try:
                out[venue] = fn()
            except Exception as exc:  # noqa: BLE001 — one venue down is a missing row
                log.debug("%s spot failed: %s", venue, exc)
                out[venue] = None
        return out

    def next_event(self) -> Optional[dict]:
        data = self._get(f"{KALSHI}/events", {"series_ticker": self.series, "status": "open",
                                              "with_nested_markets": "true", "limit": 50})
        now = self.clock()
        best = None
        for e in data.get("events") or []:
            closes = [_ts(m.get("close_time")) for m in e.get("markets") or []]
            closes = [c for c in closes if c and c > now]
            if closes and (best is None or min(closes) < best[0]):
                best = (min(closes), e)
        return best[1] if best else None

    def record_hour(self, lead_min: float = 20.0, interval: float = 5.0, band: float = 0.01) -> int:
        ev = self.next_event()
        if not ev:
            return 0
        close = min(_ts(m["close_time"]) for m in ev["markets"])
        start = close - lead_min * 60
        if self.clock() < start:
            self.sleep(start - self.clock())
        spot = self.spot()
        s0 = spot.get("kraken") or spot.get("coinbase")
        if not s0:
            return 0
        chosen = [m for m in ev["markets"] if m.get("strike_type") == "greater" and m.get("floor_strike")
                  and abs(math.log(float(m["floor_strike"]) / s0)) <= band]
        for m in chosen:
            self.conn.execute("INSERT OR IGNORE INTO markets VALUES (?,?,?,?,?,NULL)",
                              (m["ticker"], ev["event_ticker"], self.series, float(m["floor_strike"]), close))
        rows = 0
        while self.clock() < close - 1:
            t0 = self.clock()
            for venue, px in self.spot().items():
                if px:
                    self.conn.execute("INSERT INTO spot VALUES (?,?,?,?)", (self.clock(), self.series, venue, px))
            for m in chosen:
                try:
                    bid, ask, bq, aq = kalshi_top(self._get(f"{KALSHI}/markets/{m['ticker']}/orderbook"))
                except Exception as exc:  # noqa: BLE001
                    log.debug("book failed %s: %s", m["ticker"], exc)
                    continue
                self.conn.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?)",
                                  (self.clock(), m["ticker"], bid, ask, bq, aq))
                rows += 1
            self.conn.commit()
            self.sleep(max(0.0, interval - (self.clock() - t0)))
        return rows

    def settle(self) -> int:
        n = 0
        for (ticker,) in self.conn.execute("SELECT ticker FROM markets WHERE result IS NULL AND close_ts < ?",
                                           (self.clock() - 300,)).fetchall():
            try:
                m = self._get(f"{KALSHI}/markets/{ticker}").get("market") or {}
            except Exception:  # noqa: BLE001
                continue
            if m.get("result") in ("yes", "no"):
                self.conn.execute("UPDATE markets SET result=? WHERE ticker=?", (m["result"], ticker))
                n += 1
        self.conn.commit()
        return n


def report(db: str = DB, series: str = "KXBTCD", taus=(10, 5, 3, 2, 1), margin: float = 0.0) -> dict:
    """Score recorded rows: Kraken spot seconds old, sigma from the recorded spot."""
    conn = sqlite3.connect(db)
    spot = conn.execute("SELECT ts, price FROM spot WHERE series=? AND venue='kraken' ORDER BY ts",
                        (series,)).fetchall()
    out = {}
    for tau in taus:
        bm, bb, vals, groups = [], [], [], []
        for ticker, event, strike, close, result in conn.execute(
                "SELECT ticker, event, strike, close_ts, result FROM markets WHERE series=? AND result IS NOT NULL",
                (series,)):
            t = close - tau * 60
            q = conn.execute("SELECT bid, ask FROM quotes WHERE ticker=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                             (ticker, t)).fetchone()
            prior = [p for ts, p in spot if t - 900 <= ts <= t]
            if not q or q[0] is None or q[1] is None or len(prior) < 20:
                continue
            s = prior[-1]
            # per-minute sigma from 5-second returns, scaled by sqrt(12)
            rets = [math.log(b / a) for a, b in zip(prior, prior[1:]) if a > 0 and b > 0]
            sig = statistics.pstdev(rets) * math.sqrt(60.0 / 5.0) if len(rets) > 5 else None
            p = model_prob(s, strike, sig, tau) if sig else None
            if p is None:
                continue
            y = 1.0 if result == "yes" else 0.0
            bid, ask = q
            mid = (bid + ask) / 2
            if 0.03 < mid < 0.97:
                bm.append((p - y) ** 2)
                bb.append((mid - y) ** 2)
            if p - ask - taker_fee(ask) > margin:
                vals.append(y - ask - taker_fee(ask))
                groups.append(event)
            if (1 - p) - (1 - bid) - taker_fee(1 - bid) > margin:
                vals.append((1 - y) - (1 - bid) - taker_fee(1 - bid))
                groups.append(event)
        m, se = _clustered(vals, groups)
        out[tau] = {"n": len(bm), "brier_model": statistics.mean(bm) if bm else None,
                    "brier_book": statistics.mean(bb) if bb else None,
                    "taker_n": len(vals), "taker_mean": m, "taker_se": se}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--series", default="KXBTCD", choices=sorted(PAIRS))
    ap.add_argument("--hours", type=int, default=6)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.report:
        for tau, r in report(series=args.series).items():
            print(tau, r)
        return
    rec = Recorder(args.series)
    for _ in range(args.hours):
        log.info("recorded %d book rows; settled %d", rec.record_hour(), rec.settle())
    time.sleep(360)
    log.info("settled %d", rec.settle())


if __name__ == "__main__":
    main()
