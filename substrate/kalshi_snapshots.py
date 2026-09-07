#!/usr/bin/env python3
"""Milestone 2 — Kalshi decision-time snapshot service. READ-ONLY, shadow mode.

Polls PUBLIC Kalshi market data (no auth headers exist in this file, GET only)
for the configured series — weather dailies by default — and persists
decision-time YES prices to sqlite. Every snapshot row is sealed with a SHA-256
hash at write time (seal-before-resolve: the earliest snapshot per market is the
max-lead decision-time probability; nothing is ever backfilled).

Settlement pass (--settle) records outcomes for settled markets; --export writes
resolved markets to the ingest.py schema (baseline_prob is a market placeholder
until a climatology/GenCast baseline exists — do not present it as independent).

Usage:
  python3 kalshi_snapshots.py --once                # one snapshot cycle
  python3 kalshi_snapshots.py --interval 1800       # poll loop (respects a 0.5s/series pause)
  python3 kalshi_snapshots.py --settle              # record outcomes for settled markets
  python3 kalshi_snapshots.py --export weather.csv  # ingest-schema CSV of resolved markets
  python3 kalshi_snapshots.py --status              # row counts + latest snapshots
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_SERIES = "KXHIGHNY,KXHIGHCHI,KXHIGHMIA,KXHIGHAUS,KXHIGHDEN,KXHIGHLAX,KXHIGHPHIL"
PAUSE_BETWEEN_CALLS = 0.5  # public rate limits are generous; stay well under them

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    series TEXT NOT NULL,
    ticker TEXT NOT NULL,
    yes_bid REAL, yes_ask REAL, mid REAL, last_price REAL,
    volume INTEGER, open_interest INTEGER,
    close_time TEXT, expiration_time TEXT,
    seal TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snap_ticker_ts ON snapshots (ticker, ts);
CREATE TABLE IF NOT EXISTS markets (
    ticker TEXT PRIMARY KEY,
    series TEXT, title TEXT,
    close_time TEXT, expiration_time TEXT,
    first_seen_ts REAL,
    outcome INTEGER,          -- 0/1 once settled, NULL while open
    settled_ts REAL
);
"""


def http_json(path: str, **params) -> dict:
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "substrate-snapshots/1.0 (read-only)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_markets(series: str, status: str, max_pages: int = 5) -> list[dict]:
    out, cursor = [], None
    for _ in range(max_pages):
        params = {"series_ticker": series, "status": status, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = http_json("/markets", **params)
        batch = data.get("markets", [])
        out.extend(batch)
        cursor = data.get("cursor")
        if not cursor or not batch:
            break
        time.sleep(PAUSE_BETWEEN_CALLS)
    return out


def cents(v) -> float | None:
    return None if v in (None, "") else float(v) / 100.0


def seal_row(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def snapshot_cycle(conn: sqlite3.Connection, series_list: list[str]) -> None:
    now = time.time()
    total = 0
    for series in series_list:
        try:
            markets = fetch_markets(series, status="open")
        except Exception as exc:  # noqa: BLE001 — one series failing shouldn't stop the cycle
            print(f"  {series}: fetch failed ({exc})")
            continue
        for m in markets:
            ticker = m.get("ticker", "")
            if not ticker:
                continue
            bid, ask = cents(m.get("yes_bid")), cents(m.get("yes_ask"))
            mid = (bid + ask) / 2 if (bid and ask) else cents(m.get("last_price"))
            payload = {"ticker": ticker, "ts": round(now, 3), "yes_bid": bid,
                       "yes_ask": ask, "mid": mid}
            conn.execute(
                "INSERT INTO snapshots (ts, series, ticker, yes_bid, yes_ask, mid,"
                " last_price, volume, open_interest, close_time, expiration_time, seal)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (now, series, ticker, bid, ask, mid, cents(m.get("last_price")),
                 m.get("volume"), m.get("open_interest"),
                 m.get("close_time"), m.get("expiration_time"), seal_row(payload)))
            conn.execute(
                "INSERT INTO markets (ticker, series, title, close_time, expiration_time,"
                " first_seen_ts) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(ticker) DO NOTHING",
                (ticker, series, m.get("title", ""), m.get("close_time"),
                 m.get("expiration_time"), now))
        total += len(markets)
        print(f"  {series}: {len(markets)} open markets snapshotted")
        time.sleep(PAUSE_BETWEEN_CALLS)
    conn.commit()
    print(f"cycle done: {total} snapshots @ {datetime.now(timezone.utc).isoformat(timespec='seconds')}")


def settle_cycle(conn: sqlite3.Connection, series_list: list[str]) -> None:
    now = time.time()
    updated = 0
    for series in series_list:
        try:
            markets = fetch_markets(series, status="settled")
        except Exception as exc:  # noqa: BLE001
            print(f"  {series}: settle fetch failed ({exc})")
            continue
        for m in markets:
            result = str(m.get("result", "")).lower()
            if result not in ("yes", "no"):
                continue
            cur = conn.execute(
                "UPDATE markets SET outcome=?, settled_ts=? WHERE ticker=? AND outcome IS NULL",
                (1 if result == "yes" else 0, now, m.get("ticker", "")))
            updated += cur.rowcount
        time.sleep(PAUSE_BETWEEN_CALLS)
    conn.commit()
    print(f"settlement pass: {updated} newly settled markets recorded")


def iso_to_epoch(s) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def export_ingest(conn: sqlite3.Connection, path: Path) -> None:
    """Resolved markets -> ingest schema. market_prob = EARLIEST sealed snapshot (max lead)."""
    rows = conn.execute(
        "SELECT m.ticker, m.close_time, m.expiration_time, m.outcome,"
        " (SELECT s.mid FROM snapshots s WHERE s.ticker=m.ticker AND s.mid IS NOT NULL"
        "  ORDER BY s.ts ASC LIMIT 1) AS first_mid,"
        " (SELECT s.ts FROM snapshots s WHERE s.ticker=m.ticker AND s.mid IS NOT NULL"
        "  ORDER BY s.ts ASC LIMIT 1) AS first_ts"
        " FROM markets m WHERE m.outcome IS NOT NULL").fetchall()
    import csv
    n = 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["event_id", "domain", "close_time", "resolve_time",
                    "market_prob", "baseline_prob", "outcome"])
        for ticker, close_t, exp_t, outcome, mid, first_ts in rows:
            if mid is None or not (0.0 < mid < 1.0):
                continue
            close_e = iso_to_epoch(close_t) or first_ts
            w.writerow([ticker, "weather", first_ts, iso_to_epoch(exp_t) or close_e,
                        round(mid, 6), round(mid, 6), outcome])
            n += 1
    print(f"wrote {n} resolved events -> {path}")
    print("NOTE: baseline_prob is a market placeholder (no climatology model yet);"
          " close_time column holds the decision (snapshot) time.")


def status(conn: sqlite3.Connection) -> None:
    snaps = conn.execute("SELECT COUNT(*), COUNT(DISTINCT ticker) FROM snapshots").fetchone()
    mkts = conn.execute("SELECT COUNT(*), SUM(outcome IS NOT NULL) FROM markets").fetchone()
    print(f"snapshots: {snaps[0]} rows across {snaps[1]} tickers")
    print(f"markets:   {mkts[0]} tracked, {mkts[1] or 0} settled")
    for r in conn.execute(
            "SELECT series, COUNT(*), MAX(ts) FROM snapshots GROUP BY series").fetchall():
        ts = datetime.fromtimestamp(r[2], tz=timezone.utc).isoformat(timespec="seconds") if r[2] else "-"
        print(f"  {r[0]:12s} {r[1]:6d} rows   latest {ts}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="data/kalshi_snapshots.db")
    ap.add_argument("--series", default=DEFAULT_SERIES, help="comma-separated series tickers")
    ap.add_argument("--once", action="store_true", help="one snapshot cycle, then exit")
    ap.add_argument("--interval", type=int, default=0,
                    help="loop with this many seconds between cycles (min 300)")
    ap.add_argument("--settle", action="store_true", help="record outcomes for settled markets")
    ap.add_argument("--export", metavar="CSV", help="write resolved markets to ingest schema")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    conn = open_db(Path(args.db))
    series_list = [s.strip().upper() for s in args.series.split(",") if s.strip()]

    if args.status:
        status(conn)
        return
    if args.settle:
        settle_cycle(conn, series_list)
    if args.export:
        export_ingest(conn, Path(args.export))
    if args.settle or args.export:
        return

    if args.interval:
        interval = max(300, args.interval)
        while True:
            snapshot_cycle(conn, series_list)
            settle_cycle(conn, series_list)
            time.sleep(interval)
    else:
        if not args.once:
            print("(no mode given — running one cycle; use --interval for a loop)")
        snapshot_cycle(conn, series_list)


if __name__ == "__main__":
    main()
