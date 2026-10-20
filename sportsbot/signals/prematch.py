"""Pre-match quote recorder (DATA ONLY): the forward dataset every
backtest here has had to borrow from a venue's own price history.

Every `--interval` seconds it lists the open match-winner markets on the
public, keyless feeds (Polymarket US gateway; Kalshi prod reads) for the
sports the bot trades and stores the top of book with the scheduled start.
A settler pass fills in outcomes once markets resolve. `--report` runs the
pre-registered forward test in docs/PREMATCH_FORWARD_PREREG_2026-10-12.md
(NO on tennis longshots at T - 30) and a calibration table, per venue.

Nothing here is wired into trading, and nothing here places orders. Its
job is to replace "the venue's price-history endpoint said so" with quotes
this process saw, time-stamped by this process, on markets it could have
traded, so any later pre-registered test has honest forward data.
"""

from __future__ import annotations

import argparse
import logging
import math
import sqlite3
import threading
import time
from collections import defaultdict
from typing import Optional

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    venue TEXT NOT NULL,
    market_id TEXT NOT NULL,
    sport TEXT,
    start_ts INTEGER,
    bid REAL, ask REAL,
    home TEXT, away TEXT
);
CREATE INDEX IF NOT EXISTS snap_mkt ON snapshots (venue, market_id, ts);
CREATE TABLE IF NOT EXISTS results (
    venue TEXT NOT NULL,
    market_id TEXT NOT NULL,
    settle INTEGER,
    settled_ts INTEGER,
    PRIMARY KEY (venue, market_id)
);
"""
FEES = {"polymarket_us": 0.0695, "kalshi": 0.07}
CUT_S = 1800          # the pre-registered observation point: T - 30 min
TOL_S = 600           # nearest snapshot within +-10 min of it
SETTLE_AFTER_S = 6 * 3600


class Recorder:
    def __init__(self, db_path: str, clients: dict, sports=("tennis", "baseball", "table_tennis"),
                 interval: float = 300.0, horizon: float = 3 * 3600.0) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.clients = clients            # venue name -> ExchangeClient (public reads)
        self.sports = tuple(sports)
        self.interval = interval
        # Only markets starting within `horizon` are quoted: the forward test
        # reads the snapshot nearest T - 30, and a market listed days ahead
        # (hundreds of table-tennis slots at a time) would otherwise fill the
        # database with quotes nothing reads. Markets with no known start
        # cannot be placed at T - 30 at all, so they are not recorded.
        self.horizon = horizon
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ record
    def snapshot_once(self, now: Optional[int] = None) -> int:
        now = int(time.time()) if now is None else now
        n = 0
        for venue, client in self.clients.items():
            for sport in self.sports:
                try:
                    markets = client.list_sports_markets(sport)
                except Exception as exc:
                    log.warning("%s %s discovery failed: %s", venue, sport, exc)
                    continue
                for m in markets:
                    start = int(m.start_time.timestamp()) if m.start_time else None
                    if start is None or start < now or start > now + self.horizon:
                        continue               # unknown start, started, or too far out
                    try:
                        q = client.get_quote(m)
                    except Exception as exc:
                        log.debug("%s quote failed for %s: %s", venue, m.market_id, exc)
                        continue
                    with self._lock:
                        self.conn.execute(
                            "INSERT INTO snapshots (ts, venue, market_id, sport, start_ts, bid, ask, home, away)"
                            " VALUES (?,?,?,?,?,?,?,?,?)",
                            (now, venue, m.market_id, sport, start, q.bid, q.ask, m.home, m.away))
                    n += 1
        with self._lock:
            self.conn.commit()
        return n

    def settle_once(self, now: Optional[int] = None) -> int:
        """Ask each venue for the result of every recorded market whose start
        is more than SETTLE_AFTER_S ago and has no result yet."""
        now = int(time.time()) if now is None else now
        rows = self.conn.execute(
            "SELECT DISTINCT s.venue, s.market_id FROM snapshots s"
            " LEFT JOIN results r ON r.venue = s.venue AND r.market_id = s.market_id"
            " WHERE r.market_id IS NULL AND s.start_ts IS NOT NULL AND s.start_ts < ?",
            (now - SETTLE_AFTER_S,)).fetchall()
        n = 0
        for r in rows:
            client = self.clients.get(r["venue"])
            if client is None:
                continue
            try:
                res = client.get_resolution(r["market_id"])
            except Exception as exc:
                log.debug("resolution failed %s %s: %s", r["venue"], r["market_id"], exc)
                continue
            if res is None:
                continue
            with self._lock:
                self.conn.execute(
                    "INSERT OR REPLACE INTO results (venue, market_id, settle, settled_ts) VALUES (?,?,?,?)",
                    (r["venue"], r["market_id"], int(bool(res)), now))
            n += 1
        with self._lock:
            self.conn.commit()
        return n

    def run_forever(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            try:
                n = self.snapshot_once()
                s = self.settle_once()
                log.info("prematch: %d quotes recorded, %d markets settled", n, s)
            except Exception:
                log.exception("prematch cycle failed")
            self._stop.wait(max(5.0, self.interval - (time.time() - t0)))


# ------------------------------------------------------------------ report
def _stats(x):
    n = len(x)
    if n == 0:
        return 0, 0.0, 0.0
    mu = sum(x) / n
    sd = math.sqrt(sum((v - mu) ** 2 for v in x) / (n - 1)) if n > 1 else 0.0
    return n, mu, (mu / (sd / math.sqrt(n)) if sd > 0 else 0.0)


def observations(conn) -> list[dict]:
    """One row per settled market: the snapshot nearest T - 30 (within TOL_S)."""
    rows = conn.execute(
        "SELECT s.venue, s.market_id, s.sport, s.start_ts, s.ts, s.bid, s.ask, r.settle"
        " FROM snapshots s JOIN results r ON r.venue = s.venue AND r.market_id = s.market_id"
        " WHERE s.start_ts IS NOT NULL AND s.bid IS NOT NULL AND s.ask IS NOT NULL").fetchall()
    best: dict[tuple, dict] = {}
    for r in rows:
        d = dict(r)
        off = abs((d["start_ts"] - CUT_S) - d["ts"])
        if off > TOL_S:
            continue
        key = (d["venue"], d["market_id"])
        if key not in best or off < best[key]["_off"]:
            d["_off"] = off
            best[key] = d
    out = []
    for d in best.values():
        mid = (d["bid"] + d["ask"]) / 2
        if d["ask"] - d["bid"] > 0.10 or not (0.03 <= mid <= 0.97):
            continue
        d["mid"] = mid
        out.append(d)
    return out


def forward_test(conn, min_n: int = 150) -> dict:
    """Pre-registered: NO on tennis longshots (mid <= 0.15) at T - 30, held to
    settlement, net of the venue's taker fee; FAV and DOG mirrors reported
    alongside; calibration by bucket for every venue/sport."""
    obs = observations(conn)
    cells: dict[tuple, list[float]] = defaultdict(list)
    buckets: dict[tuple, list[float]] = defaultdict(list)
    for d in obs:
        fee = FEES.get(d["venue"], 0.07)
        f = lambda p: fee * p * (1 - p)   # noqa: E731
        s = float(d["settle"])
        if d["mid"] <= 0.15:
            p = 1 - d["bid"]
            cells[(d["venue"], d["sport"], "LONG-NO")].append((1 - s) - p - f(p))
            q = d["ask"]
            cells[(d["venue"], d["sport"], "DOG")].append(s - q - f(q))
        if d["mid"] >= 0.85:
            p = d["ask"]
            cells[(d["venue"], d["sport"], "FAV")].append(s - p - f(p))
        b = ("<.15" if d["mid"] < 0.15 else "<.35" if d["mid"] < 0.35 else "<.65" if d["mid"] < 0.65
             else "<.85" if d["mid"] < 0.85 else ">=.85")
        buckets[(d["venue"], d["sport"], b)].append(s - d["mid"])
    out = {"n_obs": len(obs), "cells": {}, "calibration": {}, "verdict": "PENDING"}
    for key, xs in cells.items():
        n, mu, t = _stats(xs)
        out["cells"]["/".join(key)] = {"n": n, "mean_cents": round(mu * 100, 2), "t": round(t, 2)}
    for key, xs in buckets.items():
        n, mu, _ = _stats(xs)
        out["calibration"]["/".join(key)] = {"n": n, "diff_cents": round(mu * 100, 2)}
    primary = out["cells"].get("polymarket_us/tennis/LONG-NO")
    if primary and primary["n"] >= min_n:
        out["verdict"] = ("PASS" if primary["mean_cents"] > 0 and primary["t"] >= 2 else "FAIL")
    return out


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Pre-match quote recorder (data only; never trades)")
    ap.add_argument("--db", default="data/prematch.sqlite")
    ap.add_argument("--interval", type=float, default=300.0)
    ap.add_argument("--horizon", type=float, default=3 * 3600.0,
                    help="only quote markets starting within this many seconds")
    ap.add_argument("--venues", default="polymarket_us,kalshi")
    ap.add_argument("--report", action="store_true", help="print the forward test and exit")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.report:
        import json

        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        print(json.dumps(forward_test(conn), indent=1))
        return
    clients = {}
    for v in args.venues.split(","):
        if v == "polymarket_us":
            from sportsbot.exchanges.polymarket_us import PolymarketUSClient

            clients[v] = PolymarketUSClient()
        elif v == "kalshi":
            from sportsbot.exchanges.kalshi import KalshiClient

            clients[v] = KalshiClient(env="prod")
    rec = Recorder(args.db, clients, interval=args.interval, horizon=args.horizon)
    if args.once:
        print(rec.snapshot_once(), "quotes;", rec.settle_once(), "settled")
        return
    rec.run_forever()


if __name__ == "__main__":
    main()
