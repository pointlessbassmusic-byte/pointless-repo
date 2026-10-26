"""Polymarket US trade tape: the authenticated markets websocket, recorded.

Polymarket US publishes no keyless trade tape. Trades stream only over
`wss://api.polymarket.us/v1/ws/markets` with a TRADE subscription
(docs.polymarket.us, api-reference/websocket/markets, read 2026-10-19).
The handshake carries the REST signing headers, the signature covering
`timestamp + "GET" + "/v1/ws/markets"`. Up to 100 market slugs go in one
subscription, and the server sends `{"heartbeat": {}}` to keep the
connection open.

Without this tape a resting paper order on the venue never fills (no tape,
no fill), and the live maker test has no fill-share denominator (the
executor's tape shadow). With it:

* `TapeCollector` subscribes to every moneyline the client discovers for the
  configured sports, starting within `window_h` or in play. It stores each
  print in YES terms (the venue's prices are long-side throughout) with the
  aggressor's side from the taker's order intent. Lists are refreshed and new
  slugs subscribed every `refresh_s`; on any drop it reconnects with
  exponential backoff.
* `TapeStore` records every connection as a session and every
  subscription's start, so it can say whether a slug's tape is COMPLETE over
  a window. `PolymarketUSClient.recent_trades` raises `TapeGap` when it is
  not. The executor then leaves `tape_through` absent and the paper venue
  fills nothing, instead of reading a gap as "no prints".
* The first raw trade messages are kept (`raw_samples`) so the first session
  with keys can check the field names and the price frame against the docs.
  The docs show camelCase with string enums on one page and snake_case on
  another; the parser accepts both. `verify` (CLI `us-tape --verify`) does
  that check: every sampled trade must parse with a known taker intent, and
  each slug's latest print must equal the venue's public last trade (`/bbo`,
  long frame), not 1 - p.

DATA ONLY: it reads, it never places orders. It needs POLYMARKET_US_KEY_ID /
POLYMARKET_US_SECRET_KEY, and without them the CLI exits cleanly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable, Optional

from sportsbot.core.types import TapePrint
from sportsbot.exchanges.base import TapeGap  # noqa: F401 — re-exported
from sportsbot.exchanges.polymarket_us import API, _dt, _px, sign_headers

log = logging.getLogger(__name__)

WS_PATH = "/v1/ws/markets"
WS_URL = API.replace("https://", "wss://") + WS_PATH
TRADE = "SUBSCRIPTION_TYPE_TRADE"
MAX_PER_SUB = 100
DEFAULT_DB = os.path.join("data", "us_tape.sqlite")
ENV_DB = "SPORTSBOT_US_TAPE_DB"
COVERAGE_GRACE_S = 120.0      # a session counts as alive this long after its last message
SUB_LAG_S = 5.0               # coverage starts this long after a subscribe is sent (conservative)
RAW_SAMPLES = 50

# The aggressor's order intent -> which side it bought, in the YES frame.
TAKER_SIDE = {
    "ORDER_INTENT_BUY_LONG": "yes", "ORDER_INTENT_SELL_SHORT": "yes",
    "ORDER_INTENT_SELL_LONG": "no", "ORDER_INTENT_BUY_SHORT": "no",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS prints (
    slug TEXT, ts REAL, price_yes REAL, size REAL, taker_side TEXT, trade_id TEXT,
    UNIQUE (slug, ts, price_yes, size, trade_id)
);
CREATE INDEX IF NOT EXISTS prints_slug_ts ON prints (slug, ts);
CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS subs (session_id INTEGER, slug TEXT, since REAL, PRIMARY KEY (session_id, slug));
CREATE TABLE IF NOT EXISTS raw_samples (ts REAL, body TEXT);
"""


def _get(d: dict, *names: str) -> Any:
    for n in names:
        if n in d:
            return d[n]
    return None


def parse_trade(msg: dict) -> Optional[tuple[str, TapePrint]]:
    """(slug, print) from one markets-websocket message; None for anything
    that is not a well-formed trade."""
    t = msg.get("trade") if isinstance(msg, dict) else None
    if not isinstance(t, dict):
        return None
    slug = _get(t, "marketSlug", "market_slug")
    price = _px(_get(t, "price", "px"))
    qty = _px(_get(t, "quantity", "qty"))
    ts = _dt(_get(t, "tradeTime", "trade_time", "transactTime", "transact_time"))
    if not slug or price is None or qty is None or ts is None or not 0.0 < price < 1.0 or qty <= 0:
        return None
    taker = _get(t, "taker") or {}
    intent = _get(taker, "intent") if isinstance(taker, dict) else None
    trade_id = _get(t, "tradeId", "trade_id", "execId", "exec_id")
    return str(slug), TapePrint(ts=ts, price_yes=price, size=qty, taker_side=TAKER_SIDE.get(str(intent)),
                                trade_id=str(trade_id or ""))


def subscribe_messages(slugs: list[str], prefix: str) -> list[dict]:
    """One TRADE subscription per 100 slugs (the venue's limit)."""
    out = []
    for i in range(0, len(slugs), MAX_PER_SUB):
        out.append({"subscribe": {"requestId": f"{prefix}-{i // MAX_PER_SUB}", "subscriptionType": TRADE,
                                  "marketSlugs": slugs[i:i + MAX_PER_SUB]}})
    return out


class TapeStore:
    def __init__(self, path: str = DEFAULT_DB, clock: Callable[[], float] = time.time):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        # the recorder writes while the bot reads: WAL lets them overlap
        self.conn = sqlite3.connect(path, timeout=10.0)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.clock = clock

    # --- writing (collector) ------------------------------------------------
    def open_session(self) -> int:
        now = self.clock()
        cur = self.conn.execute("INSERT INTO sessions (started, last_seen) VALUES (?, ?)", (now, now))
        self.conn.commit()
        return int(cur.lastrowid)

    def touch(self, session_id: int) -> None:
        self.conn.execute("UPDATE sessions SET last_seen=? WHERE id=?", (self.clock(), session_id))

    def subscribed(self, session_id: int, slugs: Iterable[str]) -> None:
        since = self.clock() + SUB_LAG_S
        self.conn.executemany("INSERT OR IGNORE INTO subs VALUES (?,?,?)",
                              [(session_id, s, since) for s in slugs])
        self.conn.commit()

    def add(self, slug: str, p: TapePrint) -> bool:
        cur = self.conn.execute("INSERT OR IGNORE INTO prints VALUES (?,?,?,?,?,?)",
                                (slug, p.ts.timestamp(), p.price_yes, p.size, p.taker_side, p.trade_id))
        return cur.rowcount > 0

    def sample(self, body: str) -> None:
        n = self.conn.execute("SELECT COUNT(*) FROM raw_samples").fetchone()[0]
        if n < RAW_SAMPLES:
            self.conn.execute("INSERT INTO raw_samples VALUES (?,?)", (self.clock(), body))

    def commit(self) -> None:
        self.conn.commit()

    # --- reading (client / reports) -----------------------------------------
    def covered(self, slug: str, since: float, until: Optional[float] = None) -> bool:
        """True when one connection was subscribed to `slug` from at or before
        `since` and still alive at `until` (default now)."""
        until = self.clock() if until is None else until
        row = self.conn.execute(
            "SELECT 1 FROM subs s JOIN sessions x ON x.id = s.session_id "
            "WHERE s.slug=? AND s.since <= ? AND x.last_seen >= ? LIMIT 1",
            (slug, since, until - COVERAGE_GRACE_S)).fetchone()
        return row is not None

    def prints(self, slug: str, since: float) -> list[TapePrint]:
        rows = self.conn.execute("SELECT ts, price_yes, size, taker_side, trade_id FROM prints "
                                 "WHERE slug=? AND ts >= ? ORDER BY ts DESC", (slug, since)).fetchall()
        return [TapePrint(ts=datetime.fromtimestamp(ts, timezone.utc), price_yes=px, size=sz,
                          taker_side=side, trade_id=tid or "") for ts, px, sz, side, tid in rows]

    def status(self) -> dict:
        n, slugs = self.conn.execute("SELECT COUNT(*), COUNT(DISTINCT slug) FROM prints").fetchone()
        last = self.conn.execute("SELECT MAX(last_seen), COUNT(*) FROM sessions").fetchone()
        subs = self.conn.execute("SELECT COUNT(DISTINCT slug) FROM subs").fetchone()[0]
        return {"prints": n, "slugs_with_prints": slugs, "slugs_subscribed": subs,
                "sessions": last[1], "last_seen": last[0],
                "alive": bool(last[0]) and self.clock() - last[0] <= COVERAGE_GRACE_S}


FRAME_MIN_SKEW = 0.02   # within this of 0.5 a price cannot tell YES from 1 - YES
FRAME_PX_TOL = 0.0005


def verify(store: TapeStore, bbo: Callable[[str], dict], max_slugs: int = 40) -> dict:
    """The first keyed session's check, without reading raw JSON by hand.

    Fields: every sampled trade message must parse, and every taker intent
    must be one TAKER_SIDE knows. Frame: when the tape has covered a slug
    without a gap from its latest recorded print until now, that print IS the
    venue's last trade, so it must equal the public `/bbo` `lastTradePx`
    (long frame), not 1 - p. (`/bbo`'s `lastPriceSample.ts` is a sampling
    clock that advances with no trade — 15 of 17 markets on Oct 25 — so it
    cannot identify a trade.) `ok` needs at least one confirming print and no
    problem."""
    trades = parsed = 0
    unparsed: set[str] = set()
    intents: set[str] = set()
    for (body,) in store.conn.execute("SELECT body FROM raw_samples").fetchall():
        try:
            msg = json.loads(body)
        except (TypeError, ValueError):
            continue                     # a sample cut at 4,000 chars is not a field problem
        t = msg.get("trade") if isinstance(msg, dict) else None
        if not isinstance(t, dict):
            continue
        trades += 1
        if parse_trade(msg) is None:
            unparsed.update(t.keys())
        else:
            parsed += 1
        taker = _get(t, "taker")
        intent = _get(taker, "intent") if isinstance(taker, dict) else None
        if intent is not None and str(intent) not in TAKER_SIDE:
            intents.add(str(intent))
    frame = {"agree": 0, "flipped": 0, "other_trade": 0, "not_covered": 0, "near_half": 0}
    latest = store.conn.execute(
        "SELECT slug, MAX(ts), price_yes FROM prints GROUP BY slug ORDER BY MAX(ts) DESC LIMIT ?",
        (max_slugs,)).fetchall()
    for slug, ts, px in latest:
        if not store.covered(slug, ts):
            frame["not_covered"] += 1
            continue
        try:
            last_px = (bbo(slug) or {}).get("last_px")
        except Exception:  # noqa: BLE001 — one unreadable market is a non-match, not a crash
            last_px = None
        if last_px is None:
            frame["not_covered"] += 1
        elif abs(px - 0.5) < FRAME_MIN_SKEW:
            frame["near_half"] += 1
        elif abs(px - last_px) <= FRAME_PX_TOL:
            frame["agree"] += 1
        elif abs((1.0 - px) - last_px) <= FRAME_PX_TOL:
            frame["flipped"] += 1
        else:
            frame["other_trade"] += 1    # e.g. a print between the recorder's write and this read
    problems = []
    if trades == 0:
        problems.append("no trade message recorded yet")
    if parsed < trades:
        problems.append(f"{trades - parsed} of {trades} trade messages did not parse; "
                        f"fields seen: {', '.join(sorted(unparsed))}")
    if intents:
        problems.append(f"unknown taker intents: {', '.join(sorted(intents))}")
    if frame["flipped"]:
        problems.append(f"{frame['flipped']} prints match the venue's last trade as 1 - p: "
                        "the tape is not in YES terms")
    if trades and not frame["agree"]:
        problems.append("price frame unconfirmed: no covered print matched the venue's last trade yet "
                        "(run it while the recorder is live)")
    return {"ok": not problems, "trade_samples": trades, "parsed": parsed, "frame": frame,
            "slugs_checked": len(latest), "problems": problems}


def default_store() -> Optional[TapeStore]:
    """The tape the client reads: $SPORTSBOT_US_TAPE_DB, else data/us_tape.sqlite
    when it exists (the collector's default path); None when there is none."""
    path = os.environ.get(ENV_DB) or (DEFAULT_DB if os.path.exists(DEFAULT_DB) else "")
    return TapeStore(path) if path else None


class TapeCollector:
    def __init__(self, store: TapeStore, key_id: str, secret_key: str,
                 slugs_fn: Callable[[], list[str]],
                 connect: Optional[Callable[..., Any]] = None,
                 refresh_s: float = 900.0, idle_s: float = 90.0, max_backoff: float = 300.0,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], float] = time.time):
        self.store, self.key_id, self.secret_key = store, key_id, secret_key
        self.slugs_fn, self.refresh_s, self.idle_s, self.max_backoff = slugs_fn, refresh_s, idle_s, max_backoff
        self._connect, self.sleep, self.clock = connect, sleep, clock
        self.stats = {"connects": 0, "prints": 0, "errors": 0}

    def _open(self):
        headers = sign_headers(self.key_id, self.secret_key, "GET", WS_PATH)
        if self._connect is not None:
            return self._connect(WS_URL, headers)
        import websockets   # optional dependency: `pip install -e .[maker]`

        return websockets.connect(WS_URL, additional_headers=headers, ping_interval=20, max_size=2 ** 22)

    async def _slugs(self, initial: bool) -> list[str]:
        """The discovery call, run in a thread. Errors are caught INSIDE the
        thread: some (StopIteration) cannot be set on a future and would
        leave the await hanging. A failed refresh keeps the connection and
        the slugs already subscribed; a failed first call drops the session."""
        def call():
            try:
                return sorted(set(self.slugs_fn())), None
            except Exception as exc:  # noqa: BLE001
                return None, exc
        got, exc = await asyncio.to_thread(call)
        if exc is not None:
            if initial:
                raise RuntimeError(f"slug discovery failed: {exc!r}")
            log.warning("us-tape: slug refresh failed: %r", exc)
            return []
        return got

    async def _subscribe(self, ws, session: int, slugs: list[str], prefix: str) -> None:
        for m in subscribe_messages(slugs, prefix):
            await ws.send(json.dumps(m))
        self.store.subscribed(session, slugs)

    async def session(self, stop_at: float = float("inf")) -> None:
        """One connection: subscribe, record, refresh, until it drops or idles."""
        slugs = await self._slugs(initial=True)
        async with self._open() as ws:
            self.stats["connects"] += 1
            sid = self.store.open_session()
            done: set[str] = set()
            if slugs:
                await self._subscribe(ws, sid, slugs, f"t{sid}")
                done.update(slugs)
            log.info("us-tape: session %d subscribed to %d slugs", sid, len(done))
            next_refresh = self.clock() + self.refresh_s
            n_ref = 0
            while self.clock() < stop_at:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self.idle_s)
                except asyncio.TimeoutError:
                    log.warning("us-tape: no message in %.0f s; reconnecting", self.idle_s)
                    return
                self.store.touch(sid)
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if isinstance(msg, dict) and msg.get("error"):
                    self.stats["errors"] += 1
                    log.warning("us-tape: %s", str(msg.get("error"))[:200])
                if isinstance(msg, dict) and "trade" in msg:
                    self.store.sample(str(raw)[:4000])
                    got = parse_trade(msg)
                    if got and self.store.add(*got):
                        self.stats["prints"] += 1
                self.store.commit()
                if self.clock() >= next_refresh:
                    next_refresh = self.clock() + self.refresh_s
                    new = sorted(set(await self._slugs(initial=False)) - done)
                    if new:
                        n_ref += 1
                        await self._subscribe(ws, sid, new, f"t{sid}r{n_ref}")
                        done.update(new)
                        log.info("us-tape: +%d slugs (now %d)", len(new), len(done))

    async def run(self, hours: float = 0.0) -> dict:
        stop_at = self.clock() + hours * 3600 if hours > 0 else float("inf")
        backoff = 1.0
        while self.clock() < stop_at:
            t0 = self.clock()
            try:
                await self.session(stop_at)
            except Exception as exc:  # noqa: BLE001 — any drop is a reconnect, never a crash
                self.stats["errors"] += 1
                log.warning("us-tape: connection failed: %s", exc)
            if self.clock() - t0 > 60:
                backoff = 1.0            # it ran a while: reconnect promptly
            if self.clock() >= stop_at:
                break
            await self.sleep(backoff + random.random())
            backoff = min(self.max_backoff, backoff * 2)
        return self.stats


def slugs_for(client, sports: Iterable[str], window_h: float = 24.0, inplay_h: float = 4.0) -> Callable[[], list[str]]:
    """Moneyline slugs starting within `window_h`, or started under `inplay_h` ago."""
    def fn() -> list[str]:
        now = datetime.now(timezone.utc)
        out = []
        for sport in sports:
            try:
                markets = client.list_sports_markets(sport)
            except Exception as exc:  # noqa: BLE001 — one sport's discovery failing skips that sport
                log.warning("us-tape: discovery failed for %s: %s", sport, exc)
                continue
            for m in markets:
                st = m.start_time
                if st is None or now - timedelta(hours=inplay_h) <= st <= now + timedelta(hours=window_h):
                    out.append(m.market_id)
        return out
    return fn
