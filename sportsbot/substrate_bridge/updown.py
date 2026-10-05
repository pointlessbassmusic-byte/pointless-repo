"""Crypto Up/Down window logger: the live 5-minute Polymarket BTC (or ETH)
Up/Down book sampled every few seconds alongside a public spot feed. DATA
ONLY — nothing here trades; it exists to measure whether the book lags the
near-certain outcome in the last minute by more than the fee, which is the
"near-resolution capture" the X posts describe and which minute-level
price history cannot see.

Resolution (from the market description): Chainlink BTC/USD 60-second TWAP
at the window end >= the price at the window start -> Up. Spot here is
Coinbase/Kraken/Binance.US, a proxy for Chainlink; the gap between them is
part of what the analysis has to absorb.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import defaultdict

log = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
FEE = 0.07

SCHEMA = """
CREATE TABLE IF NOT EXISTS windows (
    ep INTEGER PRIMARY KEY, asset TEXT NOT NULL, slug TEXT NOT NULL, token_up TEXT NOT NULL,
    open_spot REAL, outcome_up INTEGER, resolved_ts REAL
);
CREATE TABLE IF NOT EXISTS ticks (
    ts REAL NOT NULL, ep INTEGER NOT NULL, spot REAL,
    bid REAL, ask REAL, bid_size REAL, ask_size REAL
);
CREATE INDEX IF NOT EXISTS ticks_ep ON ticks(ep, ts);
"""


def fee(p: float) -> float:
    return FEE * p * (1 - p)


def spot_price(http, asset: str = "BTC") -> float | None:
    """Best-effort spot from public feeds, first that answers."""
    for url, parse in (
        (f"https://api.coinbase.com/v2/prices/{asset}-USD/spot", lambda j: float(j["data"]["amount"])),
        (f"https://api.binance.us/api/v3/ticker/price?symbol={asset}USDT", lambda j: float(j["price"])),
    ):
        try:
            r = http.get(url, timeout=5)
            if r.status_code == 200:
                return parse(r.json())
        except Exception:  # noqa: BLE001
            continue
    return None


def current_window(http, asset: str = "BTC", now: float | None = None) -> dict | None:
    now = now or time.time()
    ep = int(now) // 300 * 300
    slug = f"{asset.lower()}-updown-5m-{ep}"
    evs = http.get(f"{GAMMA}/events", params={"slug": slug}).json()
    if not evs:
        return None
    m = evs[0]["markets"][0]
    toks = m.get("clobTokenIds")
    toks = json.loads(toks) if isinstance(toks, str) else toks
    return {"ep": ep, "slug": slug, "token_up": str(toks[0]), "condition": m.get("conditionId")}


def resolution(http, slug: str) -> int | None:
    evs = http.get(f"{GAMMA}/events", params={"slug": slug}).json()
    if not evs:
        return None
    m = evs[0]["markets"][0]
    prices = m.get("outcomePrices")
    prices = json.loads(prices) if isinstance(prices, str) else prices
    if not prices or not m.get("closed"):
        return None
    try:
        p0 = float(prices[0])
    except (TypeError, ValueError):
        return None
    return 1 if p0 == 1.0 else 0 if p0 == 0.0 else None


class UpDownLogger:
    def __init__(self, db_path: str = "data/updown.sqlite", asset: str = "BTC", http=None):
        import httpx
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)
        self.asset = asset
        self.http = http or httpx.Client(timeout=10)

    def run(self, minutes: float = 60.0, interval: float = 3.0) -> dict:
        t_end = time.time() + minutes * 60
        cur = None
        ticks = windows = 0
        pending: list[str] = []
        while time.time() < t_end:
            now = time.time()
            ep = int(now) // 300 * 300
            if cur is None or cur["ep"] != ep:
                try:
                    cur = current_window(self.http, self.asset, now)
                except Exception as exc:  # noqa: BLE001
                    log.debug("window lookup failed: %s", exc)
                    cur = None
                if cur:
                    spot = spot_price(self.http, self.asset)
                    self.conn.execute("INSERT OR IGNORE INTO windows(ep, asset, slug, token_up, open_spot) VALUES (?,?,?,?,?)",
                                      (cur["ep"], self.asset, cur["slug"], cur["token_up"], spot))
                    self.conn.commit()
                    windows += 1
                    pending.append(cur["slug"])
                    # resolve older windows
                    for s in list(pending):
                        e = int(s.rsplit("-", 1)[-1])
                        if e + 300 + 90 < now:
                            try:
                                res = resolution(self.http, s)
                            except Exception:  # noqa: BLE001
                                res = None
                            if res is not None:
                                self.conn.execute("UPDATE windows SET outcome_up=?, resolved_ts=? WHERE ep=?", (res, now, e))
                                self.conn.commit()
                                pending.remove(s)
                            elif e + 1800 < now:
                                pending.remove(s)
            if cur:
                spot = spot_price(self.http, self.asset)
                bid = ask = bs = as_ = None
                try:
                    b = self.http.get(f"{CLOB}/book", params={"token_id": cur["token_up"]}).json()
                    bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])), key=lambda x: -x[0])
                    asks = sorted(((float(x["price"]), float(x["size"])) for x in b.get("asks", [])), key=lambda x: x[0])
                    if bids:
                        bid, bs = bids[0]
                    if asks:
                        ask, as_ = asks[0]
                except Exception as exc:  # noqa: BLE001
                    log.debug("book failed: %s", exc)
                self.conn.execute("INSERT INTO ticks VALUES (?,?,?,?,?,?,?)", (now, cur["ep"], spot, bid, ask, bs, as_))
                self.conn.commit()
                ticks += 1
            time.sleep(max(0.0, interval - (time.time() - now)))
        # final resolution sweep for anything still pending and old enough
        for s in list(pending):
            e = int(s.rsplit("-", 1)[-1])
            if e + 390 < time.time():
                try:
                    res = resolution(self.http, s)
                except Exception:  # noqa: BLE001
                    res = None
                if res is not None:
                    self.conn.execute("UPDATE windows SET outcome_up=?, resolved_ts=? WHERE ep=?", (res, time.time(), e))
        self.conn.commit()
        return {"windows": windows, "ticks": ticks}


def report(db_path: str = "data/updown.sqlite") -> dict:
    """Does the book lag the near-certain outcome? For each tick in a resolved
    window: the spot move since the window open (sign = the likely outcome),
    seconds left, and the ask on the side the spot move favours. Buying that
    side at its ask and holding to resolution has net value
    (won - ask - fee); report it by seconds-left and |move| buckets."""
    conn = sqlite3.connect(db_path)
    wins = {r[0]: r for r in conn.execute("SELECT ep, open_spot, outcome_up FROM windows WHERE outcome_up IS NOT NULL AND open_spot IS NOT NULL")}
    rows = conn.execute("SELECT ts, ep, spot, bid, ask FROM ticks WHERE bid IS NOT NULL AND ask IS NOT NULL AND spot IS NOT NULL ORDER BY ts").fetchall()
    cells: dict = defaultdict(lambda: {"n": 0, "val": 0.0, "won": 0, "ask": 0.0})
    for ts, ep, spot, bid, ask in rows:
        w = wins.get(ep)
        if not w:
            continue
        _, open_spot, up = w
        left = ep + 300 - ts
        if left < 0 or left > 300:
            continue
        move_bps = (spot - open_spot) / open_spot * 1e4
        if abs(move_bps) < 0.5:
            continue
        fav_up = move_bps > 0
        entry = ask if fav_up else (1 - bid)          # buy the favoured side at its ask
        if not (0.02 < entry < 0.995):
            continue
        won = (up == 1) == fav_up
        lb = 15 if left <= 15 else 30 if left <= 30 else 60 if left <= 60 else 120 if left <= 120 else 300
        mb = 1 if abs(move_bps) < 2 else 5 if abs(move_bps) < 5 else 10 if abs(move_bps) < 10 else 99
        c = cells[(lb, mb)]
        c["n"] += 1
        c["val"] += (1.0 if won else 0.0) - entry - fee(entry)
        c["won"] += won
        c["ask"] += entry
    out = {"windows_resolved": len(wins), "ticks": len(rows), "cells": {}}
    for k, c in cells.items():
        if c["n"]:
            out["cells"][k] = {"n": c["n"], "net_per_contract": c["val"] / c["n"], "win_rate": c["won"] / c["n"], "mean_entry": c["ask"] / c["n"]}
    return out


def format_report(r: dict) -> str:
    lines = [f"resolved windows {r['windows_resolved']}, ticks {r['ticks']}",
             f"{'secs left <=':>12} {'|move| bps <':>12} {'n':>6} {'win':>6} {'entry':>6} {'net/contract':>12}"]
    for (lb, mb), c in sorted(r["cells"].items()):
        lines.append(f"{lb:>12} {mb:>12} {c['n']:>6} {c['win_rate']:>6.3f} {c['mean_entry']:>6.3f} {c['net_per_contract']:>+12.4f}")
    return "\n".join(lines)
