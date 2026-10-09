"""Live fill markouts: what the market did right after each fill.

Settlement tells you whether a bet won; it does not tell you whether the
fill was bad. A maker fill that is 2c under water five seconds later was
picked off, whatever the match result. `docs/MARKOUT_2026-09-23.md` measured
this on tapes; this module measures it on the bot's OWN fills, which is the
one thing the live pilot buys that no simulation can (reports/ "Prediction
market profitable edges", step 1).

Every booked fill schedules marks at HORIZONS seconds. A daemon thread
quotes the market at each due time and records the mid, framed to the side
bought (YES: mid, NO: 1 - mid), so `markout = mark - entry` is signed the
same way for both sides: negative means the price moved against the fill.
Rows outlive restarts (the market is stored as JSON so the quote can be
re-requested), a quote that cannot be obtained within GRACE_S of its due
time is recorded as missed, and the board/status summarise mean markout by
sport and horizon per book. Data only: nothing here changes a decision.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import defaultdict
from typing import Optional

from sportsbot.core.types import Exchange, MarketInfo, Side

log = logging.getLogger(__name__)

HORIZONS = (5, 60, 300, 1800)
GRACE_S = 600.0          # after this long past due, a mark is recorded as missed
POLL_S = 1.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS fill_marks (
    id INTEGER PRIMARY KEY,
    bet_id INTEGER NOT NULL,
    market_id TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    horizon_s INTEGER NOT NULL,
    due_ts REAL NOT NULL,
    market_json TEXT,
    mark_ts REAL,
    mid REAL,
    mark_price REAL,
    markout REAL
);
CREATE INDEX IF NOT EXISTS fill_marks_due ON fill_marks (mark_ts, due_ts);
"""


def _market_json(market: MarketInfo) -> str:
    return json.dumps({"exchange": market.exchange.value, "market_id": market.market_id,
                       "yes_token_id": market.yes_token_id, "no_token_id": market.no_token_id,
                       "slug": market.slug})


def _market_from_json(s: str) -> MarketInfo:
    d = json.loads(s)
    return MarketInfo(exchange=Exchange(d["exchange"]), market_id=d["market_id"],
                      yes_token_id=d.get("yes_token_id"), no_token_id=d.get("no_token_id"),
                      slug=d.get("slug") or "")


class MarkoutRecorder:
    def __init__(self, store, exchange, horizons=HORIZONS) -> None:
        self.store = store
        self.exchange = exchange            # anything with get_quote(MarketInfo)
        self.horizons = tuple(horizons)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        with store._lock:
            store.conn.executescript(SCHEMA)
            store.conn.commit()

    # ------------------------------------------------------------ writes
    def schedule(self, bet_id: int, market: MarketInfo, side: Side | str,
                 entry_price: float, now: Optional[float] = None) -> int:
        """Called by the executor for every booked fill increment."""
        now = time.time() if now is None else now
        side_v = side.value if isinstance(side, Side) else str(side)
        rows = [(bet_id, market.market_id, side_v, float(entry_price), h, now + h,
                 _market_json(market)) for h in self.horizons]
        with self.store._lock:
            self.store.conn.executemany(
                "INSERT INTO fill_marks (bet_id, market_id, side, entry_price, horizon_s,"
                " due_ts, market_json) VALUES (?,?,?,?,?,?,?)", rows)
            self.store.conn.commit()
        return len(rows)

    def process_due(self, now: Optional[float] = None) -> int:
        """Mark every due row with one quote per market. Returns rows marked."""
        now = time.time() if now is None else now
        with self.store._lock:      # shared connection; the main thread writes
            rows = self.store.conn.execute(
                "SELECT * FROM fill_marks WHERE mark_ts IS NULL AND due_ts <= ?"
                " ORDER BY due_ts LIMIT 200", (now,)).fetchall()
        if not rows:
            return 0
        quotes: dict[str, Optional[float]] = {}
        done = 0
        for r in rows:
            r = dict(r)
            mid = quotes.get(r["market_id"], "unquoted")
            if mid == "unquoted":
                mid = self._mid(r)
                quotes[r["market_id"]] = mid
            if mid is None:
                if now - r["due_ts"] < GRACE_S:
                    continue                       # retry next poll
                self._write(r["id"], now, None, None, None)
                done += 1
                continue
            mark = mid if r["side"].lower() == "yes" else 1.0 - mid
            self._write(r["id"], now, mid, mark, mark - r["entry_price"])
            done += 1
        return done

    def _mid(self, row: dict) -> Optional[float]:
        try:
            market = _market_from_json(row["market_json"]) if row.get("market_json") else \
                MarketInfo(exchange=self.exchange.exchange, market_id=row["market_id"])
            q = self.exchange.get_quote(market)
        except Exception as exc:
            log.warning("markout quote failed for %s: %s", row["market_id"], exc)
            return None
        if q.bid is None or q.ask is None:
            return None
        return (q.bid + q.ask) / 2.0

    def _write(self, row_id: int, ts: float, mid, mark, markout) -> None:
        with self.store._lock:
            self.store.conn.execute(
                "UPDATE fill_marks SET mark_ts=?, mid=?, mark_price=?, markout=? WHERE id=?",
                (ts, mid, mark, markout, row_id))
            self.store.conn.commit()

    # ------------------------------------------------------------ thread
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="markout", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.process_due()
            except Exception:
                log.exception("markout loop error")
            self._stop.wait(POLL_S)


# ----------------------------------------------------------------- report
def markout_report(store, mode: Optional[str] = None) -> dict:
    """Mean markout (cents per share) by sport and horizon for one book,
    with n and a t-stat; `missed` counts marks no quote could be had for."""
    try:
        with store._lock:
            rows = store.conn.execute(
                "SELECT f.bet_id AS bet_id, b.sport AS sport, COALESCE(b.mode,'paper') AS mode,"
                " f.horizon_s AS h,"
                " f.markout AS markout, f.mark_ts AS mark_ts, f.mid AS mid"
                " FROM fill_marks f JOIN bets b ON b.id = f.bet_id"
                " WHERE f.mark_ts IS NOT NULL").fetchall()
    except Exception:
        return {"by_sport": {}, "n_fills": 0}
    cells: dict[tuple[str, int], list[float]] = defaultdict(list)
    missed: dict[tuple[str, int], int] = defaultdict(int)
    fills: set[int] = set()
    for r in rows:
        if mode is not None and r["mode"] != mode:
            continue
        fills.add(int(r["bet_id"]))
        key = (r["sport"] or "unknown", int(r["h"]))
        if r["mid"] is None:
            missed[key] += 1
        else:
            cells[key].append(float(r["markout"]))
    out: dict[str, dict[int, dict]] = defaultdict(dict)
    for (sport, h), xs in cells.items():
        n = len(xs)
        mu = sum(xs) / n
        sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
        out[sport][h] = {"n": n, "mean_cents": round(mu * 100, 3),
                         "t": round(mu / (sd / math.sqrt(n)), 2) if sd > 0 else None,
                         "missed": missed.get((sport, h), 0)}
    for (sport, h), m in missed.items():
        out[sport].setdefault(h, {"n": 0, "mean_cents": None, "t": None, "missed": m})
    n_fills = len(fills)
    return {"by_sport": {s: dict(sorted(v.items())) for s, v in out.items()},
            "n_fills": n_fills}
