"""Forward test of fading fresh-wallet long-shot buys (Result 21b). DATA
ONLY: public reads, a paper record, no orders.

The 120-day study found that brand-new Polymarket wallets buying long shots
in size lose: −9.7 ± 2.7 pt per share at settlement. The inversion (buy the
other side 10 minutes later) would have made about +8 pt after costs, but
it was looked at after the fact on 176 clustered markets. This is its own
pre-registered test, written 2026-10-14 before the first row:

* signal: a taker BUY >= $1,000 at <= 0.35 in a non-sports, non-crypto
  market (wallet_review's categoriser), from a wallet whose first-ever
  activity is <= 14 days old, first per (wallet, market, outcome);
* fade: `delay` seconds later, buy the OTHER outcome at its best ask
  (CLOB book) plus nothing, paying the market's taker fee; skipped when
  the book is one-sided or the market has closed;
* score at settlement: fade P&L per share = payout - ask - fee;
* the bar: after >= 200 fades across >= 60 markets, mean P&L > 0 with
  t >= 2 clustered by market; anything else is no edge, and a mean <= 0
  kills it.

Run continuously (`sportsbot fresh-watch --hours 0` = forever, polling the
public trade feed every `poll` seconds) on the server; `--report` scores.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import statistics
import time
from collections import defaultdict
from typing import Optional

from sportsbot.backtest import wallet_review as wr

log = logging.getLogger(__name__)

TRADES = "https://data-api.polymarket.com/trades"
ACTIVITY = "https://data-api.polymarket.com/activity"
GAMMA = "https://gamma-api.polymarket.com/markets"
BOOK = "https://clob.polymarket.com/book"
MIN_CASH, MAX_PRICE, FRESH_DAYS = 1_000, 0.35, 14
EXCLUDED = ("sports", "crypto")

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    wallet TEXT, cid TEXT, idx INTEGER, ts REAL, price REAL, cash REAL, title TEXT, category TEXT,
    other_token TEXT, fee_rate REAL, first_seen REAL,
    fade_ts REAL, fade_ask REAL, payout REAL,
    PRIMARY KEY (wallet, cid, idx)
);
CREATE TABLE IF NOT EXISTS seen_trades (tx TEXT PRIMARY KEY, ts REAL);
"""


class Watch:
    def __init__(self, db: str = "data/fresh_watch.sqlite", http=None, clock=time.time, sleep=time.sleep,
                 delay: float = 600.0):
        self.conn = sqlite3.connect(db)
        self.conn.executescript(SCHEMA)
        if http is None:
            import httpx
            http = httpx.Client(timeout=30, headers={"User-Agent": "sportsbot/1.0"})
        self.http, self.clock, self.sleep, self.delay = http, clock, sleep, delay
        self._first: dict[str, Optional[float]] = {}
        self._markets: dict[str, Optional[dict]] = {}

    def _get(self, url, params):
        for wait in (1.0, 2.0, 4.0, None):
            r = self.http.get(url, params=params)
            if r.status_code == 429 and wait is not None:
                self.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        return None

    def market(self, cid: str) -> Optional[dict]:
        if cid not in self._markets:
            rows = (self._get(GAMMA, [("condition_ids", cid), ("closed", "false")]) or []) + \
                   (self._get(GAMMA, [("condition_ids", cid), ("closed", "true")]) or [])
            m = rows[0] if rows else None
            if m:
                mk = wr.Market.from_gamma(m)
                ev = (m.get("events") or [{}])[0] or {}
                m = {"market": mk, "category": wr.category(str(ev.get("slug") or mk.slug), str(m.get("question") or ""))}
            self._markets[cid] = m
        return self._markets[cid]

    def first_seen(self, wallet: str) -> Optional[float]:
        if wallet not in self._first:
            a = self._get(ACTIVITY, {"user": wallet, "limit": 1, "sortBy": "TIMESTAMP",
                                     "sortDirection": "ASC"}) or []
            self._first[wallet] = float(a[0]["timestamp"]) if a else None
        return self._first[wallet]

    # ------------------------------------------------------------ steps
    def scan(self) -> int:
        """New qualifying trades from the public feed (newest first)."""
        rows = self._get(TRADES, {"filterType": "CASH", "filterAmount": MIN_CASH, "takerOnly": "true",
                                  "limit": 500}) or []
        n = 0
        for t in rows:
            tx = f"{t.get('transactionHash')}:{t.get('asset')}"
            if self.conn.execute("SELECT 1 FROM seen_trades WHERE tx=?", (tx,)).fetchone():
                continue
            self.conn.execute("INSERT OR IGNORE INTO seen_trades VALUES (?,?)", (tx, float(t["timestamp"])))
            p = float(t.get("price") or 1)
            if str(t.get("side")).upper() != "BUY" or p > MAX_PRICE or p * float(t.get("size") or 0) < MIN_CASH:
                continue
            idx = int(t.get("outcomeIndex") or 0)
            if idx > 1:
                continue
            info = self.market(str(t["conditionId"]))
            if not info or info["category"] in EXCLUDED or len(info["market"].tokens) != 2:
                continue
            first = self.first_seen(str(t["proxyWallet"]))
            ts = float(t["timestamp"])
            if first is None or ts - first > FRESH_DAYS * 86400:
                continue
            mk = info["market"]
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO signals(wallet,cid,idx,ts,price,cash,title,category,other_token,fee_rate,"
                "first_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (t["proxyWallet"], mk.condition_id, idx, ts, p, p * float(t["size"]), str(t.get("title") or ""),
                 info["category"], mk.tokens[1 - idx], mk.fee_rate, first))
            n += cur.rowcount
        # the feed is newest-first and polled every minute: two days of keys is plenty
        self.conn.execute("DELETE FROM seen_trades WHERE ts < ?", (self.clock() - 2 * 86400,))
        self.conn.commit()
        return n

    def price_fades(self) -> int:
        """The other side's best ask, once `delay` has passed."""
        n = 0
        due = self.conn.execute("SELECT wallet, cid, idx, other_token FROM signals WHERE fade_ts IS NULL AND ts <= ?",
                                (self.clock() - self.delay,)).fetchall()
        for wallet, cid, idx, tok in due:
            try:
                book = self._get(BOOK, {"token_id": tok}) or {}
            except Exception as exc:  # noqa: BLE001 — closed market or no book: recorded as unpriceable
                log.debug("book %s: %s", tok[:10], exc)
                book = {}
            asks = [float(a["price"]) for a in book.get("asks") or [] if float(a.get("size") or 0) > 0]
            ask = min(asks) if asks else None
            self.conn.execute("UPDATE signals SET fade_ts=?, fade_ask=? WHERE wallet=? AND cid=? AND idx=?",
                              (self.clock(), ask if ask is not None and 0 < ask < 1 else -1.0, wallet, cid, idx))
            n += 1
        self.conn.commit()
        return n

    def settle(self) -> int:
        n = 0
        for cid, idx in self.conn.execute("SELECT DISTINCT cid, idx FROM signals WHERE payout IS NULL "
                                          "AND fade_ask > 0").fetchall():
            self._markets.pop(cid, None)
            info = self.market(cid)
            if not info:
                continue
            payouts = info["market"].payouts
            if len(payouts) == 2 and payouts[1 - idx] is not None:
                self.conn.execute("UPDATE signals SET payout=? WHERE cid=? AND idx=?", (payouts[1 - idx], cid, idx))
                n += 1
        self.conn.commit()
        return n

    def run(self, hours: float = 0.0, poll: float = 60.0, settle_every: float = 3600.0) -> None:
        end = self.clock() + hours * 3600 if hours > 0 else math.inf
        last_settle = -math.inf
        while self.clock() < end:
            try:
                new = self.scan()
                priced = self.price_fades()
                if self.clock() - last_settle >= settle_every:
                    log.info("settled %d", self.settle())
                    last_settle = self.clock()
                if new or priced:
                    log.info("fresh-watch: %d new signals, %d fades priced", new, priced)
            except Exception as exc:  # noqa: BLE001 — a failed poll is retried next minute
                log.warning("fresh-watch poll failed: %s", exc)
            self.sleep(poll)


def report(db: str = "data/fresh_watch.sqlite") -> dict:
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT cid, fade_ask, payout, fee_rate FROM signals WHERE fade_ask > 0 "
                        "AND payout IS NOT NULL").fetchall()
    total = conn.execute("SELECT COUNT(*), COUNT(DISTINCT cid) FROM signals").fetchone()
    vals = [(payout - ask - (fee or 0.05) * ask * (1 - ask), cid) for cid, ask, payout, fee in rows]
    out = {"signals": total[0], "markets": total[1], "graded": len(vals)}
    if vals:
        m = statistics.mean(v for v, _ in vals)
        by: dict[str, float] = defaultdict(float)
        for v, g in vals:
            by[g] += v - m
        se = math.sqrt(sum(x * x for x in by.values())) / len(vals) if len(vals) > 1 else None
        out.update(mean=m, se=se, t=(m / se if se else None), graded_markets=len(by))
        out["verdict"] = ("killed" if m <= 0 and len(vals) >= 200 else
                          "alive" if len(vals) >= 200 and len(by) >= 60 and se and m / se >= 2 else
                          "collecting")
    return out


def format_report(r: dict) -> str:
    return json.dumps(r, indent=1, default=str)


def status_line(db: str) -> Optional[str]:
    """One line for the digest and the board; None when no record exists."""
    import os
    if not os.path.exists(db):
        return None
    try:
        r = report(db)
    except sqlite3.Error:
        return None
    line = (f"fresh-wallet fade (forward test): {r['signals']} signals in {r['markets']} markets, "
            f"{r['graded']} graded")
    if r.get("mean") is not None:
        se = f" ± {r['se'] * 100:.1f}" if r.get("se") else ""
        line += f", {r['mean'] * 100:+.1f}{se} pt per share; {r['verdict']} (bar: 200 fades, 60 markets, t >= 2)"
    return line
