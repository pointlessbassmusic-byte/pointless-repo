#!/usr/bin/env python3
"""
l2_replay.py — offline maker-parameter sweep against REAL recorded L2 events.

Input:  l2_events.jsonl.gz produced by tennis_maker_bot.py (every book snapshot,
        depth delta, trade print, tick change, timestamped).
Output: (1) a data-quality report — was fill opportunity even present?
        (2) a PRE-REGISTERED 24-combo grid, run ONCE, 60/40 fit/holdout by time.

Honesty guards (same rules as the prior harnesses):
  - The grid below is fixed before looking at results. Do not re-run with new
    grids on the same data and keep the best one — that is curve fitting.
  - Believe HOLDOUT columns only, and only with 30+ fills.
  - The queue model is conservative (cancels never help us; repricing resets
    queue), but replay still ignores our own market impact — treat positive
    results as "earns continued paper quoting," never as "go live" by itself.

Usage:
  python3 l2_replay.py l2_events.jsonl.gz
  python3 l2_replay.py --self-test
"""

from __future__ import annotations

import gzip
import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

FEE_RATE = 0.05
REBATE_RATE = 0.15
MIN_MID, MAX_MID = 0.15, 0.85
QUOTE_USD = 5.0
POSITION_TTL = 900.0

# ---- PRE-REGISTERED GRID (fixed before running; run once per dataset) ----
GRID = [
    dict(min_spread_ticks=ms, tp_ticks=tp, sl_ticks=sl, order_ttl=ttl)
    for ms in (2, 3)
    for tp in (1, 2, 3)
    for sl in (3, 5)
    for ttl in (120, 300)
]


def taker_fee(p: float, sh: float) -> float:
    return FEE_RATE * p * (1 - p) * sh


def rebate(p: float, sh: float) -> float:
    return REBATE_RATE * taker_fee(p, sh)


# ---------------- book + conservative queue (mirrors the live bot) ---------

class Book:
    __slots__ = ("bids", "asks", "tick")

    def __init__(self) -> None:
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.tick = 0.01

    def snapshot(self, bids, asks) -> None:
        self.bids = {float(x["price"]): float(x["size"]) for x in bids}
        self.asks = {float(x["price"]): float(x["size"]) for x in asks}
        for p in list(self.bids) + list(self.asks):
            s = f"{p:.4f}".rstrip("0")
            if "." in s and len(s.split(".")[-1]) >= 3:
                self.tick = 0.001
                break

    def change(self, price: float, side: str, size: float) -> None:
        d = self.bids if side.upper() == "BUY" else self.asks
        if size <= 0:
            d.pop(price, None)
        else:
            d[price] = size

    def bb(self) -> Optional[float]:
        return max(self.bids) if self.bids else None

    def ba(self) -> Optional[float]:
        return min(self.asks) if self.asks else None

    def mid_spread(self) -> Tuple[Optional[float], Optional[float]]:
        b, a = self.bb(), self.ba()
        if b is None or a is None:
            return None, None
        return (b + a) / 2, a - b


@dataclass
class Order:
    side: str
    price: float
    size: float
    queue: float
    ts: float
    filled: float = 0.0

    def rem(self) -> float:
        return max(self.size - self.filled, 0.0)

    def on_trade(self, tp: float, tsz: float, aggr: str) -> float:
        if self.rem() <= 0:
            return 0.0
        if self.side == "BUY":
            rel = aggr == "SELL" and tp <= self.price + 1e-9
            thru = tp < self.price - 1e-9
        else:
            rel = aggr == "BUY" and tp >= self.price - 1e-9
            thru = tp > self.price + 1e-9
        if not rel:
            return 0.0
        if thru:
            f = self.rem()
            self.filled += f
            return f
        if self.queue > 0:
            burn = min(self.queue, tsz)
            self.queue -= burn
            tsz -= burn
        if tsz <= 0:
            return 0.0
        f = min(self.rem(), tsz)
        self.filled += f
        return f


# ---------------- per-token simulator for one param combo ------------------

class Sim:
    def __init__(self, prm: dict):
        self.p = prm
        self.book = Book()
        self.entry: Optional[Order] = None
        self.tp: Optional[Order] = None
        self.pos_px = 0.0
        self.pos_sz = 0.0
        self.pos_ts = 0.0
        self.pos_rebate = 0.0
        self.trades: List[Tuple[float, float]] = []  # (entry_ts, pnl)

    def _quote(self, ts: float) -> None:
        if self.pos_sz > 0 or self.entry is not None:
            return
        mid, spr = self.book.mid_spread()
        if mid is None or not (MIN_MID <= mid <= MAX_MID):
            return
        if spr < self.p["min_spread_ticks"] * self.book.tick - 1e-9:
            return
        bb, ba = self.book.bb(), self.book.ba()
        px = round(bb + self.book.tick, 4)
        if px >= ba - 1e-9:
            px = bb
        self.entry = Order("BUY", px, QUOTE_USD / px,
                           self.book.bids.get(px, 0.0), ts)

    def _risk(self, ts: float) -> None:
        if self.entry:
            o = self.entry
            bb = self.book.bb()
            if ts - o.ts > self.p["order_ttl"] or \
               (bb is not None and bb - o.price > 2 * self.book.tick + 1e-9):
                self.entry = None
        if self.pos_sz <= 0:
            return
        bb = self.book.bb()
        if bb is None:
            return
        sl = self.pos_px - self.p["sl_ticks"] * self.book.tick
        ttl = ts - self.pos_ts > POSITION_TTL
        if bb <= sl + 1e-9 or ttl:
            pnl = (bb - self.pos_px) * self.pos_sz - taker_fee(bb, self.pos_sz) \
                + self.pos_rebate
            self.trades.append((self.pos_ts, pnl))
            self.pos_sz = 0.0
            self.tp = None

    def _entry_fill(self, f: float, ts: float) -> None:
        o = self.entry
        rb = rebate(o.price, f)
        if self.pos_sz <= 0:
            self.pos_px, self.pos_sz, self.pos_ts, self.pos_rebate = o.price, f, ts, rb
        else:
            tot = self.pos_sz + f
            self.pos_px = (self.pos_px * self.pos_sz + o.price * f) / tot
            self.pos_sz = tot
            self.pos_rebate += rb
        if o.rem() <= 1e-9:
            self.entry = None
        if self.tp is None:
            tp_px = round(self.pos_px + self.p["tp_ticks"] * self.book.tick, 4)
            self.tp = Order("SELL", tp_px, self.pos_sz,
                            self.book.asks.get(tp_px, 0.0), ts)

    def _tp_fill(self, f: float, ts: float) -> None:
        o = self.tp
        pnl = (o.price - self.pos_px) * f + rebate(o.price, f) \
            + self.pos_rebate * (f / self.pos_sz if self.pos_sz else 0)
        self.trades.append((self.pos_ts, pnl))
        self.pos_sz = max(0.0, self.pos_sz - f)
        if self.pos_sz <= 1e-9:
            self.pos_sz = 0.0
            self.tp = None
        elif o.rem() <= 1e-9:
            self.tp = None

    def feed(self, ev: tuple) -> None:
        ts, et, payload = ev
        if et == "book":
            self.book.snapshot(payload[0], payload[1])
        elif et == "chg":
            for pr, sd, sz in payload:
                self.book.change(pr, sd, sz)
        elif et == "tick":
            self.book.tick = payload
        elif et == "trade":
            pr, sz, aggr = payload
            if self.entry:
                f = self.entry.on_trade(pr, sz, aggr)
                if f > 0:
                    self._entry_fill(f, ts)
            if self.tp:
                f = self.tp.on_trade(pr, sz, aggr)
                if f > 0:
                    self._tp_fill(f, ts)
        self._risk(ts)
        self._quote(ts)


# ---------------- loading + reporting --------------------------------------

def load(path: str):
    """Parse the recorder stream into per-token compact event lists."""
    per: Dict[str, List[tuple]] = defaultdict(list)
    n = trades = 0
    t0, t1 = None, None
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = m.get("_ts")
            tok = str(m.get("asset_id") or m.get("assetId") or "")
            et = m.get("e") or m.get("event_type")
            if ts is None or not tok or not et:
                continue
            t0 = ts if t0 is None else min(t0, ts)
            t1 = ts if t1 is None else max(t1, ts)
            n += 1
            if et == "book":
                per[tok].append((ts, "book",
                                 (m.get("bids") or m.get("buys") or [],
                                  m.get("asks") or m.get("sells") or [])))
            elif et == "price_change":
                chs = [(float(c["price"]), c["side"], float(c["size"]))
                       for c in m.get("changes") or [] if "price" in c]
                if chs:
                    per[tok].append((ts, "chg", chs))
            elif et == "tick_size_change":
                try:
                    per[tok].append((ts, "tick", float(m.get("new_tick_size"))))
                except (TypeError, ValueError):
                    pass
            elif et == "last_trade_price":
                try:
                    per[tok].append((ts, "trade",
                                     (float(m["price"]), float(m.get("size") or 0),
                                      str(m.get("side") or "").upper())))
                    trades += 1
                except (KeyError, ValueError):
                    pass
    return per, n, trades, t0, t1


def data_report(per, n, trades, t0, t1) -> None:
    hours = (t1 - t0) / 3600 if t0 is not None else 0
    print(f"Events: {n:,}   trade prints: {trades:,}   tokens: {len(per)}   "
          f"coverage: {hours:.1f}h")
    # opportunity scan: time-weighted spread condition inside the mid band
    op_tokens = 0
    for tok, evs in per.items():
        b = Book()
        ok = False
        for ts, et, pl in evs:
            if et == "book":
                b.snapshot(*pl)
            elif et == "chg":
                for pr, sd, sz in pl:
                    b.change(pr, sd, sz)
            mid, spr = b.mid_spread()
            if mid and MIN_MID <= mid <= MAX_MID and spr >= 2 * b.tick - 1e-9:
                ok = True
                break
        op_tokens += ok
    print(f"Tokens with ANY quotable moment (mid in band, spread>=2 ticks): "
          f"{op_tokens}/{len(per)}")
    if trades == 0:
        print("NOTE: zero trade prints recorded -> no fills are possible in "
              "replay; thesis remains UNTESTED on this file.")


def run_grid(per, t0, t1) -> None:
    cut = t0 + 0.6 * (t1 - t0)
    hdr = (f"{'spr':>4} {'tp':>3} {'sl':>3} {'ttl':>4} | "
           f"{'fit n':>5} {'win%':>5} {'fit$':>8} | "
           f"{'hold n':>6} {'win%':>5} {'hold$':>8}  flag")
    print("\n=== Tennis — MAKER replay (real L2, conservative queue) ===")
    print(hdr)
    print("-" * len(hdr))
    for prm in GRID:
        fit, hold = [], []
        for tok, evs in per.items():
            sim = Sim(prm)
            for ev in evs:
                sim.feed(ev)
            for ets, pnl in sim.trades:
                (fit if ets < cut else hold).append(pnl)
        def agg(xs):
            if not xs:
                return 0, 0.0, 0.0
            w = sum(1 for x in xs if x > 0)
            return len(xs), 100 * w / len(xs), sum(xs)
        fn, fw, fp = agg(fit)
        hn, hw, hp = agg(hold)
        flag = "" if hn >= 30 else "LOW-N"
        print(f"{prm['min_spread_ticks']:>4} {prm['tp_ticks']:>3} "
              f"{prm['sl_ticks']:>3} {prm['order_ttl']:>4} | "
              f"{fn:>5} {fw:>5.0f} {fp:>8.3f} | "
              f"{hn:>6} {hw:>5.0f} {hp:>8.3f}  {flag}")
    print("\nGrid fixed before running; run ONCE per dataset. Believe HOLDOUT")
    print("with 30+ fills only. Positive here = earns MORE paper quoting,")
    print("not live capital. Negative/empty = maker thesis dead on this venue.")


# ---------------- self-test (mechanics only, synthetic labeled) -------------

def self_test() -> int:
    fails = []

    def ck(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    prm = dict(min_spread_ticks=2, tp_ticks=2, sl_ticks=3, order_ttl=300)
    s = Sim(prm)
    t = 1000.0
    s.feed((t, "book", ([{"price": "0.40", "size": "20"}],
                        [{"price": "0.44", "size": "20"}])))
    ck("quotes into wide book", s.entry is not None and s.entry.price == 0.41)
    q0 = s.entry.queue
    ck("queue starts at displayed size at px", q0 == 0.0)
    # sells hit 0.41: fill us
    s.feed((t + 1, "trade", (0.41, 5.0, "SELL")))
    ck("entry fills on real sell volume", s.pos_sz > 0)
    ck("tp placed after fill", s.tp is not None and abs(s.tp.price - 0.43) < 1e-9)
    # buyer lifts through tp
    s.feed((t + 2, "trade", (0.43, 50.0, "BUY")))
    ck("tp fill closes position", s.pos_sz == 0 and len(s.trades) == 1)
    ck("tp pnl positive incl rebates", s.trades[0][1] > 0)

    s2 = Sim(prm)
    s2.feed((t, "book", ([{"price": "0.40", "size": "5"}],
                         [{"price": "0.44", "size": "5"}])))
    s2.feed((t + 1, "trade", (0.41, 3.0, "SELL")))
    ck("position open pre-SL", s2.pos_sz > 0)
    # bid collapses below SL
    s2.feed((t + 2, "chg", [(0.40, "BUY", 0.0), (0.37, "BUY", 5.0)]))
    ck("SL taker-closes", s2.pos_sz == 0 and len(s2.trades) == 1)
    ck("SL pnl negative and fee-charged", s2.trades[0][1] < 0)

    s3 = Sim(prm)
    s3.feed((t, "book", ([{"price": "0.50", "size": "2"}],
                         [{"price": "0.51", "size": "2"}])))
    ck("no quote in tight book", s3.entry is None)

    ck("grid is 24 combos", len(GRID) == 24)
    print(("\n%d failure(s)" % len(fails)) if fails else "\nAll self-tests passed.")
    return 1 if fails else 0


def main() -> None:
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]
    t_start = time.time()
    per, n, trades, t0, t1 = load(path)
    if not per or t0 is None:
        print("No usable events in", path)
        sys.exit(1)
    data_report(per, n, trades, t0, t1)
    run_grid(per, t0, t1)
    print(f"\n(replay took {time.time()-t_start:.1f}s)")


if __name__ == "__main__":
    main()
