"""Forward record: the live US sportsbook line against Polymarket US and main.
DATA ONLY -- public reads, no keys, no orders.

Result 26 (EDGE_VERDICT) found the DraftKings close via ESPN a hair sharper
than Polymarket's last pre-start print, and the taker toward it at T-15
netting +4.2 +- 2.5c per contract (t 1.7, 365 trades). It could not be
trusted for two reasons: the close is stamped at kickoff, AFTER the T-15
decision (look-ahead), and the trades were priced on Polymarket main, which
a US account cannot trade. This record fixes both:

* every `poll` seconds it reads ESPN's CURRENT moneyline for every NFL, NBA,
  NHL and MLB game (keyless core API; the CDN caches it up to ~11 minutes,
  and that staleness is part of what is measured), together with the
  Polymarket US book (public gateway) and the Polymarket main book (CLOB),
  at checkpoints 60, 30 and 15 minutes before the start. Only what was
  readable at that moment is stored, so there is no look-ahead;
* the settled result comes from ESPN's scoreboard after the game.

PRE-REGISTRATION (2026-10-20, written before the first row; the bar was set
by the user on 2026-10-20, lowered from t >= 2 to a one-sided 95% test,
t >= 1.65):

* trade: at the T-15 checkpoint, on POLYMARKET US (the venue a US account
  can use), buy the side whose de-vigged book probability exceeds that
  side's ASK by >= 2c, paying the ask plus the US taker fee 0.0695 x p x
  (1 - p); one trade per game at most; hold to settlement. Unit: cents per
  contract ($1 payout);
* the bar: after >= 100 trades, mean net > 0 with t >= 1.65 -> the rule
  goes to the pre-live gate (a minimum-size live test, never straight to
  capital); mean <= 0 after >= 100 trades -> killed; otherwise collecting;
* reported beside it, not eligible to pass: the same rule on Polymarket
  main (its own fee, 0.03 NFL/NBA, 0.05 NHL/MLB), the T-30 and T-60
  checkpoints, and 1c / 3c margins.

Run: `sportsbot book-lines` (forever; `sportsbot-booklines.service` on the
server) and `sportsbot book-lines --report`.
"""

from __future__ import annotations

import logging
import math
import os
import sqlite3
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sportsbot.backtest.espn_line_study import american_to_prob, devig, team_names

log = logging.getLogger(__name__)

CORE = "https://sports.core.api.espn.com/v2/sports"
SITE = "https://site.api.espn.com/apis/site/v2/sports"
# our key -> (ESPN path, the venue clients' sport key, Polymarket main taker rate)
SPORTS = {
    "nfl": ("football/nfl", "football", 0.03),
    "nba": ("basketball/nba", "basketball", 0.03),
    "nhl": ("hockey/nhl", "hockey", 0.05),
    "mlb": ("baseball/mlb", "baseball", 0.05),
}
CHECKPOINTS = (60, 30, 15)          # minutes before the start
PRIMARY_CHECKPOINT = 15
CHECKPOINT_SLACK_MIN = 5            # a checkpoint is taken up to 5 min after it falls
MARGIN = 0.02
US_TAKER = 0.0695
BAR_TRADES, BAR_T = 100, 1.65       # set by the user 2026-10-20 (one-sided 95%)

SCHEMA = """
CREATE TABLE IF NOT EXISTS snaps (
    game_id TEXT, sport TEXT, checkpoint INTEGER, ts REAL, start_ts REAL,
    home TEXT, away TEXT, provider TEXT, home_ml TEXT, away_ml TEXT, book_home REAL,
    us_slug TEXT, us_home_is_yes INTEGER, us_bid REAL, us_ask REAL,
    pm_id TEXT, pm_home_is_yes INTEGER, pm_bid REAL, pm_ask REAL,
    PRIMARY KEY (game_id, checkpoint)
);
CREATE TABLE IF NOT EXISTS results (game_id TEXT PRIMARY KEY, sport TEXT, home_won INTEGER, ts REAL);
"""


def _iso(s: str) -> float:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


def current_line(odds: dict) -> Optional[dict]:
    """The first provider quoting both teams' CURRENT moneylines (live
    pre-game shape: homeTeamOdds.current.moneyLine.american, falling back to
    homeTeamOdds.moneyLine)."""
    for it in odds.get("items") or []:
        h, a = it.get("homeTeamOdds") or {}, it.get("awayTeamOdds") or {}

        def ml(t):
            cur = (t.get("current") or {}).get("moneyLine")
            if isinstance(cur, dict) and cur.get("american") is not None:
                return cur["american"]
            return t.get("moneyLine")
        hm, am = ml(h), ml(a)
        p = devig(american_to_prob(hm), american_to_prob(am))
        if p is not None:
            return {"provider": (it.get("provider") or {}).get("name"), "home_ml": str(hm), "away_ml": str(am),
                    "book_home": p}
    return None


def side_price(home_is_yes: bool, bid: Optional[float], ask: Optional[float], home: bool) -> Optional[float]:
    """The price to BUY one team from a YES-frame book (YES = outcomes[0])."""
    buy_yes = home == home_is_yes
    if buy_yes:
        return ask
    return None if bid is None else round(1.0 - bid, 6)


class Recorder:
    def __init__(self, db: str = "data/book_lines.sqlite", us=None, pm=None, http=None,
                 clock: Callable[[], float] = time.time, sleep=time.sleep, sports=tuple(SPORTS)):
        d = os.path.dirname(db)
        if d:
            os.makedirs(d, exist_ok=True)
        self.conn = sqlite3.connect(db, timeout=10.0)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        if http is None:
            import httpx
            http = httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if us is None:
            from sportsbot.exchanges.polymarket_us import PolymarketUSClient
            us = PolymarketUSClient(key_id="", secret_key="")
        if pm is None:
            from sportsbot.exchanges.polymarket import PolymarketClient
            pm = PolymarketClient()
        self.http, self.us, self.pm, self.clock, self.sleep = http, us, pm, clock, sleep
        self.sports = sports
        self._boards: dict[tuple, tuple[float, list]] = {}
        self._venue: dict[tuple, tuple[float, list]] = {}

    # --------------------------------------------------------------- reads
    def _json(self, url, params=None):
        r = self.http.get(url, params=params)
        r.raise_for_status()
        return r.json()

    def games(self, sport: str) -> list[dict]:
        """ESPN events around today (cached 10 min): id, start, both teams'
        name forms, state, and the winner once final."""
        path = SPORTS[sport][0]
        now = self.clock()
        out = []
        for dd in (-1, 0, 1):
            day = datetime.fromtimestamp(now, timezone.utc) + timedelta(days=dd)
            key = (sport, day.strftime("%Y%m%d"))
            hit = self._boards.get(key)
            if hit is None or now - hit[0] > 600:
                data = self._json(f"{SITE}/{path}/scoreboard", {"dates": key[1], "limit": 100})
                rows = []
                for e in data.get("events", []):
                    comp = (e.get("competitions") or [{}])[0]
                    side = {c.get("homeAway"): c for c in comp.get("competitors", [])}
                    if "home" not in side or "away" not in side:
                        continue
                    winner = None
                    if (e.get("status") or {}).get("type", {}).get("completed"):
                        winner = bool(side["home"].get("winner"))
                    rows.append({"id": str(e["id"]), "start": _iso(e["date"]),
                                 "home": str(side["home"].get("team", {}).get("displayName", "")),
                                 "away": str(side["away"].get("team", {}).get("displayName", "")),
                                 "home_names": team_names(side["home"].get("team") or {}),
                                 "away_names": team_names(side["away"].get("team") or {}),
                                 "home_won": winner})
                self._boards[key] = (now, rows)
            out += self._boards[key][1]
        uniq = {g["id"]: g for g in out}
        return list(uniq.values())

    def _venue_markets(self, venue: str, sport: str) -> list:
        now = self.clock()
        key = (venue, sport)
        hit = self._venue.get(key)
        if hit is None or now - hit[0] > 900:
            client = self.us if venue == "us" else self.pm
            try:
                ms = client.list_sports_markets(SPORTS[sport][1])
            except Exception as exc:  # noqa: BLE001 — a venue failing leaves its columns empty
                log.warning("book-lines: %s discovery failed for %s: %s", venue, sport, exc)
                ms = []
            self._venue[key] = (now, ms)
        return self._venue[key][1]

    def match(self, venue: str, sport: str, g: dict):
        """(market, home_is_yes) for the ESPN game on a venue, or (None, None)."""
        for m in self._venue_markets(venue, sport):
            a, b = str(m.home or "").lower(), str(m.away or "").lower()
            st = m.start_time.timestamp() if m.start_time else None
            if st is not None and abs(st - g["start"]) > 3 * 3600:
                continue
            if a in g["home_names"] and b in g["away_names"]:
                return m, True
            if a in g["away_names"] and b in g["home_names"]:
                return m, False
        return None, None

    def _book(self, venue: str, m) -> tuple[Optional[float], Optional[float]]:
        try:
            q = (self.us if venue == "us" else self.pm).get_quote(m)
        except Exception as exc:  # noqa: BLE001
            log.debug("book-lines: %s quote failed for %s: %s", venue, m.market_id, exc)
            return None, None
        return q.bid, q.ask

    # --------------------------------------------------------------- steps
    def snapshot(self, sport: str, g: dict, checkpoint: int) -> bool:
        path = SPORTS[sport][0].split("/")
        try:
            odds = self._json(f"{CORE}/{path[0]}/leagues/{path[1]}/events/{g['id']}/competitions/{g['id']}/odds")
        except Exception as exc:  # noqa: BLE001
            log.debug("book-lines: odds failed for %s: %s", g["id"], exc)
            odds = {}
        line = current_line(odds) or {}
        row = {"game_id": g["id"], "sport": sport, "checkpoint": checkpoint, "ts": self.clock(),
               "start_ts": g["start"], "home": g["home"], "away": g["away"],
               "provider": line.get("provider"), "home_ml": line.get("home_ml"), "away_ml": line.get("away_ml"),
               "book_home": line.get("book_home")}
        for venue, idc in (("us", "us_slug"), ("pm", "pm_id")):
            m, home_is_yes = self.match(venue, sport, g)
            bid = ask = None
            if m is not None:
                bid, ask = self._book(venue, m)
            row[idc] = None if m is None else m.market_id
            row[f"{venue}_home_is_yes"] = None if home_is_yes is None else int(home_is_yes)
            row[f"{venue}_bid"], row[f"{venue}_ask"] = bid, ask
        cols = ",".join(row)
        self.conn.execute(f"INSERT OR IGNORE INTO snaps ({cols}) VALUES ({','.join('?' * len(row))})",
                          tuple(row.values()))
        self.conn.commit()
        return True

    def cycle(self) -> dict:
        now = self.clock()
        took, settled = 0, 0
        for sport in self.sports:
            try:
                games = self.games(sport)
            except Exception as exc:  # noqa: BLE001 — one league's scoreboard failing skips it
                log.warning("book-lines: scoreboard failed for %s: %s", sport, exc)
                continue
            for g in games:
                mins = (g["start"] - now) / 60.0
                for cp in CHECKPOINTS:
                    if cp - CHECKPOINT_SLACK_MIN <= mins <= cp and not self.conn.execute(
                            "SELECT 1 FROM snaps WHERE game_id=? AND checkpoint=?", (g["id"], cp)).fetchone():
                        took += self.snapshot(sport, g, cp)
                if g["home_won"] is not None and self.conn.execute(
                        "SELECT 1 FROM snaps WHERE game_id=?", (g["id"],)).fetchone() and not self.conn.execute(
                        "SELECT 1 FROM results WHERE game_id=?", (g["id"],)).fetchone():
                    self.conn.execute("INSERT INTO results VALUES (?,?,?,?)",
                                      (g["id"], sport, int(g["home_won"]), now))
                    settled += 1
        self.conn.commit()
        return {"snapshots": took, "settled": settled}

    def run(self, hours: float = 0.0, poll: float = 60.0) -> None:
        end = self.clock() + hours * 3600 if hours > 0 else math.inf
        while self.clock() < end:
            try:
                out = self.cycle()
                if out["snapshots"] or out["settled"]:
                    log.info("book-lines: %s", out)
            except Exception as exc:  # noqa: BLE001 — a failed pass is retried next poll
                log.warning("book-lines: cycle failed: %s", exc)
            self.sleep(poll)


# ------------------------------------------------------------------ report
def _fee(venue: str, sport: str, p: float) -> float:
    rate = US_TAKER if venue == "us" else SPORTS[sport][2]
    return rate * p * (1.0 - p)


def trades(conn, venue: str = "us", checkpoint: int = PRIMARY_CHECKPOINT, margin: float = MARGIN) -> list[dict]:
    rows = conn.execute(
        f"SELECT s.game_id, s.sport, s.book_home, s.{venue}_home_is_yes, s.{venue}_bid, s.{venue}_ask, r.home_won "
        "FROM snaps s JOIN results r ON r.game_id = s.game_id WHERE s.checkpoint=? AND s.book_home IS NOT NULL "
        f"AND s.{venue}_home_is_yes IS NOT NULL", (checkpoint,)).fetchall()
    out = []
    for gid, sport, book, hy, bid, ask, won in rows:
        best = None
        for home, p_book in ((True, book), (False, 1.0 - book)):
            price = side_price(bool(hy), bid, ask, home)
            if price is None or not 0.0 < price < 1.0:
                continue
            gap = p_book - price
            if gap >= margin and (best is None or gap > best[0]):
                best = (gap, home, price)
        if best is None:
            continue
        _, home, price = best
        paid_won = 1.0 if (bool(won) == home) else 0.0
        out.append({"game_id": gid, "sport": sport, "price": price,
                    "net": paid_won - price - _fee(venue, sport, price)})
    return out


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    m = statistics.mean(vals)
    se = statistics.stdev(vals) / math.sqrt(len(vals)) if len(vals) > 1 else None
    return {"n": len(vals), "mean": m, "se": se, "t": (m / se) if se else None}


def verdict(st: dict) -> str:
    if st.get("n", 0) < BAR_TRADES:
        return f"collecting ({st.get('n', 0)}/{BAR_TRADES} trades)"
    if st["mean"] <= 0:
        return "killed (mean <= 0 after the bar's trade count)"
    if st.get("t") is not None and st["t"] >= BAR_T:
        return "PASSES -> pre-live gate (minimum-size live test)"
    return "no edge yet (t below the bar)"


def report(db: str = "data/book_lines.sqlite") -> dict:
    conn = sqlite3.connect(db)
    n_snaps, n_games = conn.execute("SELECT COUNT(*), COUNT(DISTINCT game_id) FROM snaps").fetchone()
    n_settled = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
    out = {"snapshots": n_snaps, "games": n_games, "settled": n_settled, "rows": {}}
    prim = _stats([t["net"] for t in trades(conn, "us", PRIMARY_CHECKPOINT, MARGIN)])
    out["primary"] = dict(prim, verdict=verdict(prim))
    for venue in ("us", "pm"):
        for cp in CHECKPOINTS:
            for mg in (0.01, 0.02, 0.03):
                out["rows"][f"{venue} T-{cp} margin {mg * 100:.0f}c"] = _stats(
                    [t["net"] for t in trades(conn, venue, cp, mg)])
    return out


def format_report(r: dict) -> str:
    def f(s):
        if not s.get("n"):
            return "n 0"
        se = f" ± {s['se'] * 100:.2f}" if s.get("se") else ""
        t = f" (t {s['t']:+.2f})" if s.get("t") is not None else ""
        return f"{s['mean'] * 100:+.2f}{se}c{t} on {s['n']}"
    p = r["primary"]
    lines = [f"book-lines forward record: {r['games']} games, {r['snapshots']} snapshots, {r['settled']} settled",
             f"PRIMARY (Polymarket US, T-15, 2c margin, at the ask, net of the US taker fee): {f(p)} -> "
             f"{p['verdict']}"]
    for k, s in r["rows"].items():
        lines.append(f"  {k:22s} {f(s)}")
    return "\n".join(lines)


def status_line(db: str) -> Optional[str]:
    if not os.path.exists(db):
        return None
    try:
        r = report(db)
    except sqlite3.Error:
        return None
    p = r["primary"]
    mean = "" if not p.get("n") else f", {p['mean'] * 100:+.2f}c per contract"
    return (f"sportsbook-line forward record (Polymarket US, T-15): {r['games']} games, {r['settled']} settled, "
            f"{p.get('n', 0)} trades{mean}; {p['verdict']}")
