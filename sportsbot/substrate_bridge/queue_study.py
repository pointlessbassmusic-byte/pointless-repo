"""Queue study: does a $1 order resting at the touch ever fill on Polymarket
US, and how does that compare with Polymarket's main book on the same game?
DATA ONLY — public reads, no orders, nothing wired to the strategy.

The touch-maker's measured edge (Result 15) comes from STRICT fills: a
resting bid is credited only when a taker print goes strictly below it, i.e.
the whole level in front of it was consumed. How often that happens depends
on the venue's book. Polymarket US carries market-maker queues of 10^4-10^6
contracts at the touch on team sports; if the level is almost never swept
before the game starts, the venue's zero maker fee buys nothing.

Polymarket US publishes no keyless trade tape, but its public `/bbo` carries
cumulative `sharesTraded` and the `lastTradePx`. Sampled every `interval`
seconds that is a coarse tape: an interval "traded" when the counter rose,
and it traded THROUGH the bid sampled at its start when the last print sits
strictly below it (above the ask for the ask side). Prints that swept the
level and came back inside one interval are missed, so the US count is a
lower bound. Polymarket main is sampled the same way (its CLOB book) and its
real taker tape is stored too, so the report gives main's count on both the
coarse rule (like for like) and the exact rule (any print through).

Every price is stored in the US venue's LONG frame (its outcomes[0]); a
main-book market whose YES is the other team is flipped on the way in.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id INTEGER PRIMARY KEY,
    sport TEXT NOT NULL,
    us_slug TEXT NOT NULL UNIQUE,
    us_long TEXT NOT NULL,
    us_short TEXT NOT NULL,
    start_ts REAL NOT NULL,
    pm_condition TEXT,                 -- NULL when main lists no matching game
    pm_yes_token TEXT,
    pm_no_token TEXT,
    pm_yes_is_us_long INTEGER,
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS samples (
    ts REAL NOT NULL,
    game_id INTEGER NOT NULL,
    venue TEXT NOT NULL,               -- 'us' | 'pm'
    bid REAL, ask REAL, bid_qty REAL, ask_qty REAL,
    traded REAL,                       -- US cumulative sharesTraded; NULL for pm
    last_px REAL                       -- US lastTradePx; NULL for pm
);
CREATE INDEX IF NOT EXISTS samples_game ON samples(game_id, venue, ts);
CREATE TABLE IF NOT EXISTS prints (
    game_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    price REAL NOT NULL,               -- US long frame
    size REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS prints_game ON prints(game_id, ts);
"""

SPORTS = ("hockey", "baseball", "basketball", "football", "college_football", "tennis")
MATCH_START_TOLERANCE_H = 3.0     # same teams on another day is another game (playoff series)
EPS = 1e-9


def _key(sport: str, name: str) -> str:
    if sport == "tennis":
        from sportsbot.data.tennis_data import normalize_player
        return normalize_player(name or "")
    from sportsbot.data.mlb_data import normalize_team
    return normalize_team(name or "")


def match_main(sport: str, us_market, pm_markets) -> Optional[tuple]:
    """The main-book market for the same game: same two names, start within
    MATCH_START_TOLERANCE_H. Returns (pm MarketInfo, pm_yes_is_us_long)."""
    names = frozenset((_key(sport, us_market.home), _key(sport, us_market.away)))
    if len(names) != 2 or us_market.start_time is None:
        return None
    for p in pm_markets:
        if frozenset((_key(sport, p.home), _key(sport, p.away))) != names:
            continue
        if p.start_time is None:
            continue
        if abs((p.start_time - us_market.start_time).total_seconds()) > MATCH_START_TOLERANCE_H * 3600:
            continue
        return p, _key(sport, p.home) == _key(sport, us_market.home)
    return None


def _flip(bid, ask, bq, aq, flip: bool):
    if not flip:
        return bid, ask, bq, aq
    return (None if ask is None else round(1.0 - ask, 6), None if bid is None else round(1.0 - bid, 6),
            aq, bq)


class QueueSampler:
    def __init__(self, db_path: str = "data/queue.sqlite", us=None, pm=None, clock=time.time):
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)
        self.us = us
        self.pm = pm
        self.clock = clock
        self._games: dict[int, tuple] = {}      # game_id -> (us MarketInfo, pm MarketInfo|None, flip)
        self._tape_since: dict[int, float] = {}

    def _clients(self):
        if self.us is None:
            from sportsbot.exchanges.polymarket_us import PolymarketUSClient
            self.us = PolymarketUSClient()
        if self.pm is None:
            from sportsbot.exchanges.polymarket import PolymarketClient
            self.pm = PolymarketClient()

    # ------------------------------------------------------------ discovery
    def discover(self, sports=SPORTS, window_h: float = 12.0) -> int:
        """Every US moneyline starting within `window_h`, matched to main."""
        self._clients()
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        n = 0
        for sport in sports:
            try:
                us_markets = self.us.list_sports_markets(sport)
            except Exception as exc:  # noqa: BLE001 — one sport down: log, keep the others
                log.warning("US discovery failed for %s: %s", sport, exc)
                continue
            us_markets = [m for m in us_markets if m.start_time is not None
                          and now < m.start_time <= now + timedelta(hours=window_h)]
            if not us_markets:
                continue
            try:
                pm_markets = self.pm.list_sports_markets(sport)
            except Exception as exc:  # noqa: BLE001 — sample the US side alone
                log.warning("main discovery failed for %s: %s", sport, exc)
                pm_markets = []
            for m in us_markets:
                hit = match_main(sport, m, pm_markets)
                pmm, same = hit if hit else (None, None)
                self.conn.execute(
                    "INSERT OR IGNORE INTO games(sport,us_slug,us_long,us_short,start_ts,pm_condition,"
                    "pm_yes_token,pm_no_token,pm_yes_is_us_long,created_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (sport, m.market_id, m.home or "", m.away or "", m.start_time.timestamp(),
                     pmm.market_id if pmm else None, pmm.yes_token_id if pmm else None,
                     pmm.no_token_id if pmm else None, None if pmm is None else int(same), self.clock()))
                gid = self.conn.execute("SELECT game_id FROM games WHERE us_slug=?",
                                        (m.market_id,)).fetchone()[0]
                if gid not in self._games:
                    self._games[gid] = (m, pmm, pmm is not None and not same)
                    n += 1
        self.conn.commit()
        return n

    # ------------------------------------------------------------ sampling
    def sample_once(self) -> int:
        """One pass: US bbo + book, main book, for every game not yet started."""
        rows = []
        for gid, (um, pmm, flip) in self._games.items():
            if self.clock() >= um.start_time.timestamp():
                continue                          # pre-game only: the touch arm stops at the start
            try:
                bbo = self.us.get_bbo(um.market_id)
                q = self.us.get_quote(um)
                rows.append((self.clock(), gid, "us", q.bid, q.ask,
                             q.bids[0].size if q.bids else None, q.asks[0].size if q.asks else None,
                             bbo.get("traded"), bbo.get("last_px")))
            except Exception as exc:  # noqa: BLE001 — a missing read is a missing row
                log.debug("US read failed %s: %s", um.market_id, exc)
            if pmm is not None:
                try:
                    pq = self.pm.get_quote(pmm)
                    bid, ask, bq, aq = _flip(pq.bid, pq.ask, pq.bids[0].size if pq.bids else None,
                                             pq.asks[0].size if pq.asks else None, flip)
                    rows.append((self.clock(), gid, "pm", bid, ask, bq, aq, None, None))
                except Exception as exc:  # noqa: BLE001
                    log.debug("main read failed %s: %s", pmm.market_id, exc)
        self.conn.executemany("INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def store_tape(self, start_ts: float) -> int:
        """Main's taker prints since the last fetch (or `start_ts`), long frame,
        for every game whose pre-start window is not yet fully stored."""
        n = 0
        for gid, (um, pmm, flip) in self._games.items():
            if pmm is None:
                continue
            since = self._tape_since.get(gid, start_ts)
            if since >= um.start_time.timestamp():
                continue
            try:
                prints = self.pm.recent_trades(pmm, datetime.fromtimestamp(since, timezone.utc))
            except Exception as exc:  # noqa: BLE001
                log.debug("tape failed %s: %s", pmm.market_id, exc)
                continue
            rows = [(gid, p.ts.timestamp(), round(1.0 - p.price_yes, 6) if flip else p.price_yes, p.size)
                    for p in prints if p.ts.timestamp() > since]
            self.conn.executemany("INSERT INTO prints VALUES (?,?,?,?)", rows)
            if rows:
                self._tape_since[gid] = max(r[1] for r in rows)
            n += len(rows)
        self.conn.commit()
        return n

    def run(self, minutes: float = 120.0, interval: float = 30.0, sports=SPORTS,
            window_h: float = 12.0, rediscover_every: float = 1800.0,
            tape_every: float = 300.0, sleep=time.sleep) -> dict:
        t0 = self.clock()
        t_end = t0 + minutes * 60
        last_disc = last_tape = -math.inf
        passes = rows = prints = 0
        while self.clock() < t_end:
            if self.clock() - last_disc >= rediscover_every:
                log.info("queue study: %d new games", self.discover(sports, window_h))
                last_disc = self.clock()
            started = self.clock()
            rows += self.sample_once()
            passes += 1
            if self.clock() - last_tape >= tape_every:
                prints += self.store_tape(t0)
                last_tape = self.clock()
            sleep(max(0.0, interval - (self.clock() - started)))
        prints += self.store_tape(t0)
        return {"games": len(self._games), "passes": passes, "rows": rows, "prints": prints}


# ---------------------------------------------------------------- analysis
def _clustered(vals: list[float], groups: list[int]) -> tuple[float, float]:
    n = len(vals)
    m = statistics.mean(vals)
    by: dict[int, float] = defaultdict(float)
    for v, g in zip(vals, groups):
        by[g] += v - m
    return m, math.sqrt(sum(s * s for s in by.values())) / n


def report(db_path: str = "data/queue.sqlite", interval: float = 30.0,
           close_within_s: float = 900.0) -> dict:
    """Per venue and sport, on pre-start samples only:

    * market_hours, touch queue (median of the two sides), volume per hour;
    * through: intervals in which a print went strictly through the touch
      sampled at the interval's start (bid side below the bid, ask side above
      the ask) — a strict fill for a resting order at that touch. `us` and
      `pm_coarse` use the last print per interval; `pm_exact` any print;
    * pulled: the touch moved away (bid down, ask up) with no print through
      it — the level was cancelled, so an order left there would now sit
      alone at the front; fills from there are the adverse kind and are not
      credited here;
    * clv: closing mid (the venue's last sample, when it is within
      `close_within_s` of the start) minus the bid for a bid fill, ask minus
      the closing mid for an ask fill; mean and se clustered by game.
    """
    conn = sqlite3.connect(db_path)
    games = {g[0]: g for g in conn.execute("SELECT game_id, sport, start_ts FROM games")}
    acc: dict = defaultdict(lambda: {"games": set(), "hours": 0.0, "queue": [], "volume": 0.0,
                                     "intervals": 0, "through": 0, "pulled": 0, "clv": [], "clv_g": []})
    max_gap = 3.0 * interval
    for gid, sport, start_ts in games.values():
        prints = conn.execute("SELECT ts, price, size FROM prints WHERE game_id=? ORDER BY ts",
                              (gid,)).fetchall()
        for venue in ("us", "pm"):
            s = conn.execute("SELECT ts, bid, ask, bid_qty, ask_qty, traded, last_px FROM samples "
                             "WHERE game_id=? AND venue=? AND ts < ? ORDER BY ts",
                             (gid, venue, start_ts)).fetchall()
            if len(s) < 2:
                continue
            close = None
            last = s[-1]
            if start_ts - last[0] <= close_within_s and last[1] is not None and last[2] is not None:
                close = (last[1] + last[2]) / 2
            rules = ("us",) if venue == "us" else ("pm_coarse", "pm_exact")
            for rule in rules:
                a = acc[(rule, sport)]
                a["games"].add(gid)
                for row in s:
                    if row[3] is not None and row[4] is not None:
                        a["queue"].append((row[3] + row[4]) / 2)
                for prev, cur in zip(s, s[1:]):
                    dt = cur[0] - prev[0]
                    if dt > max_gap or prev[1] is None or prev[2] is None:
                        continue
                    a["hours"] += dt / 3600
                    a["intervals"] += 1
                    if rule == "us":
                        if prev[5] is None or cur[5] is None:
                            continue
                        vol = max(0.0, cur[5] - prev[5])
                        lasts = [cur[6]] if vol > 0 and cur[6] is not None else []
                    else:
                        inside = [p for p in prints if prev[0] < p[0] <= cur[0]]
                        vol = sum(p[2] for p in inside)
                        prices = [p[1] for p in inside]
                        lasts = prices[-1:] if rule == "pm_coarse" else prices
                    a["volume"] += vol
                    bid_hit = any(p < prev[1] - EPS for p in lasts)
                    ask_hit = any(p > prev[2] + EPS for p in lasts)
                    a["through"] += int(bid_hit) + int(ask_hit)
                    if cur[1] is not None and cur[1] < prev[1] - EPS and not bid_hit:
                        a["pulled"] += 1
                    if cur[2] is not None and cur[2] > prev[2] + EPS and not ask_hit:
                        a["pulled"] += 1
                    if close is not None:
                        if bid_hit:
                            a["clv"].append(close - prev[1])
                            a["clv_g"].append(gid)
                        if ask_hit:
                            a["clv"].append(prev[2] - close)
                            a["clv_g"].append(gid)
    out = {}
    for (rule, sport), a in sorted(acc.items()):
        row = {"games": len(a["games"]), "market_hours": round(a["hours"], 2),
               "touch_queue_median": statistics.median(a["queue"]) if a["queue"] else None,
               "volume_per_hour": a["volume"] / a["hours"] if a["hours"] else None,
               "through": a["through"],
               "through_per_market_hour": a["through"] / a["hours"] if a["hours"] else None,
               "pulled": a["pulled"],
               "clv_n": len(a["clv"])}
        if a["clv"]:
            m, se = _clustered(a["clv"], a["clv_g"])
            row.update(clv_mean=m, clv_se=se)
        out.setdefault(rule, {})[sport] = row
    return out


def format_report(r: dict) -> str:
    lines = ["rule       sport              games  mkt-h   touchQ   vol/h  through  /mkt-h  pulled   CLV/fill (n)"]
    for rule in ("us", "pm_coarse", "pm_exact"):
        for sport, x in sorted(r.get(rule, {}).items()):
            clv = (f"{x['clv_mean']:+.4f} ± {x['clv_se']:.4f} ({x['clv_n']})"
                   if x.get("clv_n") else "—")
            q = x["touch_queue_median"]
            v = x["volume_per_hour"]
            per = x["through_per_market_hour"]
            lines.append(f"{rule:10s} {sport:18s} {x['games']:5d} {x['market_hours']:6.1f} "
                         f"{(q if q is not None else float('nan')):8,.0f} {(v if v is not None else float('nan')):7,.0f} "
                         f"{x['through']:7d} {(per if per is not None else float('nan')):7.2f} {x['pulled']:7d}   {clv}")
    return "\n".join(lines)
