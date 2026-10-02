"""Kalshi Liquidity Incentive Program (LIP) shadow recorder. DATA ONLY.

Question it answers: would resting orders earn more in LIP rewards than they lose
to adverse selection when they fill? The 2026-10-01 survey showed that filled
maker orders lose money; LIP pays for resting time whether or not orders fill.
Nothing public answers the net, so it has to be measured forward.

What it records (public endpoints only, no keys, never places an order):
  programs   active liquidity programs (reward, period, discount, target size)
  snapshots  order book of a panel of rewarded markets at random times
  trades     trade tape of the panel (to simulate fills on hypothetical quotes)
  results    settlement of panel markets

Scoring follows Kalshi's published rule (help.kalshi.com, LIP article):
per side, the reference price is the level where cumulative resting size,
walking down from the best bid, first reaches target_size / 5. An order at or
better than the reference scores size x 1.0; below it, size x discount^ticks.
Our hypothetical order's share = our score / (everyone's score incl. ours).

`period_reward` is assumed to be in centi-cents (1/10,000 USD); see
REWARD_UNIT_USD. The analysis reports raw units too, so a wrong unit can be
corrected afterwards without re-recording.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sqlite3
import time
from datetime import datetime
from typing import Iterable, Optional

import httpx

log = logging.getLogger(__name__)

API = "https://api.elections.kalshi.com/trade-api/v2"
REWARD_UNIT_USD = 1e-4
TICK = 0.01

SCHEMA = """
CREATE TABLE IF NOT EXISTS programs (
  id TEXT PRIMARY KEY, market_ticker TEXT, start_ts INTEGER, end_ts INTEGER,
  period_reward INTEGER, discount_bps INTEGER, target_size REAL, seen_ts INTEGER);
CREATE TABLE IF NOT EXISTS snapshots (
  ts INTEGER, ticker TEXT, yes_bids TEXT, no_bids TEXT);
CREATE INDEX IF NOT EXISTS snap_ticker ON snapshots(ticker, ts);
CREATE TABLE IF NOT EXISTS trades (
  trade_id TEXT PRIMARY KEY, ticker TEXT, ts INTEGER, yes_price REAL,
  count REAL, taker_side TEXT);
CREATE INDEX IF NOT EXISTS trade_ticker ON trades(ticker, ts);
CREATE TABLE IF NOT EXISTS results (
  ticker TEXT PRIMARY KEY, result TEXT, settlement_value REAL, ts INTEGER);
CREATE TABLE IF NOT EXISTS panel (
  ticker TEXT PRIMARY KEY, category TEXT, added_ts INTEGER, weight REAL);
"""


def _ts(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


# ---------------------------------------------------------------- scoring (pure)

def reference_price(bids: list[tuple[float, float]], target_size: float) -> Optional[float]:
    """bids: (price, size), any order. Walk down from the best bid until the
    cumulative size reaches target_size / 5; that level is the reference.
    If the whole side is thinner than that, the worst bid is the reference."""
    if not bids:
        return None
    need = target_size / 5.0
    cum = 0.0
    for price, size in sorted(bids, key=lambda b: -b[0]):
        cum += size
        if cum >= need:
            return price
    return min(p for p, _ in bids)


def order_score(price: float, size: float, ref: Optional[float], discount: float) -> float:
    if ref is None or price >= ref - 1e-9:
        return size
    ticks = round((ref - price) / TICK)
    return size * discount ** ticks


def side_share(bids: list[tuple[float, float]], our_price: float, our_size: float,
               target_size: float, discount: float) -> float:
    """Our share of one side's score at one snapshot, with our order added to
    the book (it can move the reference price)."""
    book = list(bids) + [(our_price, our_size)]
    ref = reference_price(book, target_size)
    total = sum(order_score(p, s, ref, discount) for p, s in book)
    ours = order_score(our_price, our_size, ref, discount)
    return ours / total if total > 0 else 0.0


def simulate_fill(our_price: float, queue_ahead: float, trades: Iterable[dict],
                  side: str) -> bool:
    """Did a resting YES bid (side='yes') / NO bid (side='no') at our_price fill?
    trades: dicts with yes_price, count, taker_side, in time order after we
    posted. Trades strictly through our price always fill us; trades AT our price
    fill us only after `queue_ahead` contracts traded there (price-time priority)."""
    at_price = 0.0
    for t in trades:
        yp, n = t["yes_price"], t["count"]
        if side == "yes" and t["taker_side"] == "no":        # taker sold YES
            if yp < our_price - 1e-9:
                return True
            if abs(yp - our_price) < 1e-9:
                at_price += n
        elif side == "no" and t["taker_side"] == "yes":      # taker bought YES = sold NO
            no_px = 1.0 - yp
            if no_px < our_price - 1e-9:
                return True
            if abs(no_px - our_price) < 1e-9:
                at_price += n
        if at_price > queue_ahead:
            return True
    return False


# ---------------------------------------------------------------- recording

class Recorder:
    def __init__(self, db_path: str, panel_size: int = 400, weather_weight: float = 5.0,
                 rps: float = 3.0, client: Optional[httpx.Client] = None,
                 categories: Optional[dict] = None):
        self.db = sqlite3.connect(db_path)
        self.db.executescript(SCHEMA)
        self.panel_size, self.weather_weight, self.rps = panel_size, weather_weight, rps
        self.http = client or httpx.Client(timeout=30, headers={"User-Agent": "sportsbot-lip-recorder"})
        self.categories = categories or {}   # series ticker -> Kalshi category
        self._last = 0.0

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        wait = 1.0 / self.rps - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        for attempt in range(4):        # GET only: retries are safe
            try:
                r = self.http.get(API + path, params=params)
                if r.status_code == 429 or r.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                return r.json()
            except httpx.TransportError:
                time.sleep(2 ** attempt)
        raise RuntimeError(f"GET {path} failed")

    def refresh_programs(self) -> int:
        now, cur, n = int(time.time()), None, 0
        while True:
            p = {"status": "active", "limit": 1000}
            if cur:
                p["cursor"] = cur
            d = self._get("/incentive_programs", p)
            for x in d.get("incentive_programs", []):
                if x.get("incentive_type") != "liquidity":
                    continue
                self.db.execute(
                    "INSERT OR REPLACE INTO programs VALUES (?,?,?,?,?,?,?,?)",
                    (x["id"], x["market_ticker"], _ts(x["start_date"]), _ts(x["end_date"]),
                     int(x["period_reward"]), int(x.get("discount_factor_bps") or 0),
                     float(x.get("target_size_fp") or 0), now))
                n += 1
            cur = d.get("next_cursor")
            if not cur or not d.get("incentive_programs"):
                break
        self.db.commit()
        return n

    def refill_panel(self) -> None:
        """Keep ~panel_size live rewarded markets. Weather is oversampled; the
        weight is stored so estimates can be re-weighted."""
        now = int(time.time())
        live = {r[0] for r in self.db.execute(
            "SELECT market_ticker FROM programs WHERE start_ts<=? AND end_ts>?", (now, now))}
        panel = {r[0] for r in self.db.execute("SELECT ticker FROM panel")}
        active_panel = panel & live
        need = self.panel_size - len(active_panel)
        cands = sorted(live - panel)
        if need <= 0 or not cands:
            return
        def cat(t):
            return self.categories.get(t.split("-")[0], "?")
        w = [self.weather_weight if cat(t) == "Climate and Weather" else 1.0 for t in cands]
        picked = set()
        while len(picked) < min(need, len(cands)):
            picked.add(random.choices(cands, weights=w)[0])
        for t in picked:
            self.db.execute("INSERT OR IGNORE INTO panel VALUES (?,?,?,?)",
                            (t, cat(t), now, self.weather_weight if cat(t) == "Climate and Weather" else 1.0))
        self.db.commit()

    def snapshot(self, ticker: str) -> None:
        d = self._get(f"/markets/{ticker}/orderbook", {"depth": 30}).get("orderbook_fp") or {}
        yes = [[float(p), float(s)] for p, s in d.get("yes_dollars") or []]
        no = [[float(p), float(s)] for p, s in d.get("no_dollars") or []]
        self.db.execute("INSERT INTO snapshots VALUES (?,?,?,?)",
                        (int(time.time()), ticker, json.dumps(yes), json.dumps(no)))

    def pull_trades(self, ticker: str) -> None:
        last = self.db.execute("SELECT MAX(ts) FROM trades WHERE ticker=?", (ticker,)).fetchone()[0]
        since = last or self.db.execute("SELECT added_ts FROM panel WHERE ticker=?", (ticker,)).fetchone()[0]
        cur = None
        while True:
            p = {"ticker": ticker, "limit": 1000, "min_ts": int(since)}
            if cur:
                p["cursor"] = cur
            d = self._get("/markets/trades", p)
            for t in d.get("trades", []):
                self.db.execute("INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?)",
                                (t["trade_id"], ticker, _ts(t["created_time"]),
                                 float(t["yes_price_dollars"]), float(t["count_fp"]), t["taker_side"]))
            cur = d.get("cursor")
            if not cur or not d.get("trades"):
                break

    def pull_results(self) -> None:
        todo = [r[0] for r in self.db.execute(
            "SELECT p.ticker FROM panel p LEFT JOIN results r ON r.ticker=p.ticker WHERE r.ticker IS NULL")]
        for t in todo:
            m = self._get(f"/markets/{t}").get("market") or {}
            if m.get("status") in ("settled", "finalized") and m.get("settlement_value_dollars") is not None:
                self.db.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?)",
                                (t, m.get("result"), float(m["settlement_value_dollars"]), int(time.time())))
        self.db.commit()

    def run(self, snap_every: float = 240, trades_every: float = 900,
            programs_every: float = 1800, results_every: float = 3600) -> None:
        nxt = {"programs": 0.0, "trades": time.time() + trades_every, "results": time.time() + results_every}
        while True:
            now = time.time()
            if now >= nxt["programs"]:
                log.info("lip: %d active liquidity programs", self.refresh_programs())
                self.refill_panel()
                nxt["programs"] = now + programs_every
            tnow = int(now)
            panel = [r[0] for r in self.db.execute(
                "SELECT p.ticker FROM panel p JOIN programs g ON g.market_ticker=p.ticker "
                "WHERE g.start_ts<=? AND g.end_ts>? GROUP BY p.ticker", (tnow, tnow))]
            random.shuffle(panel)
            # spread one pass over snap_every seconds at random offsets
            for t in panel:
                try:
                    self.snapshot(t)
                except Exception:  # noqa: BLE001 — one bad market must not stop the recorder
                    log.warning("lip: snapshot failed %s", t, exc_info=True)
            self.db.commit()
            if time.time() >= nxt["trades"]:
                for t in panel:
                    try:
                        self.pull_trades(t)
                    except Exception:  # noqa: BLE001
                        log.warning("lip: trades failed %s", t, exc_info=True)
                self.db.commit()
                nxt["trades"] = time.time() + trades_every
            if time.time() >= nxt["results"]:
                self.pull_results()
                nxt["results"] = time.time() + results_every
            time.sleep(max(1.0, snap_every * random.random()))


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Kalshi LIP shadow recorder (data only)")
    ap.add_argument("--db", default="data/lip.sqlite")
    ap.add_argument("--panel", type=int, default=400)
    ap.add_argument("--rps", type=float, default=3.0)
    ap.add_argument("--series-json", default=None,
                    help="optional /series dump; fetched from the API when omitted")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # one line per request otherwise
    rec = Recorder(a.db, panel_size=a.panel, rps=a.rps)
    series = json.load(open(a.series_json)) if a.series_json else rec._get("/series").get("series", [])
    rec.categories = {s["ticker"]: s.get("category", "?") for s in series}
    rec.run()


if __name__ == "__main__":
    main()
