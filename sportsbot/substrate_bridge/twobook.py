"""Two-book logger: the same match on Kalshi and Polymarket, both order
books, same seconds. DATA ONLY — nothing here places orders or feeds the
strategy; it exists to answer one question the price histories could not:

    when the two venues disagree, does Polymarket's BOOK (not its last
    trade) follow Kalshi with a delay long enough to hit it?

EDGE_VERDICT Result 10 found Polymarket closing 37% (MLB) / 71% (tennis) of
the hourly gap toward Kalshi per hour, but the Polymarket series there is
the hourly last trade, so "converging" and "a stale print finally updated"
are indistinguishable. Sampling both books every few seconds separates
them: a stale print has a live ask sitting at the new price already; a
lagging book does not.

Pairs are matched by normalised competitor names (and date) across the two
venues' open moneylines; every sample stores both venues' top of book in
their own orientation, and the report re-orients Polymarket to Kalshi's
YES side. `report()` gives (a) the lead-lag regression at several horizons
and (b) the executable test: buy the lagging book at its ASK when the
other venue's mid is theta above it, mark to that book's mid k steps later,
net of that venue's taker fee, standard errors clustered by pair.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pairs (
    pair_id INTEGER PRIMARY KEY,
    sport TEXT NOT NULL,
    kalshi_ticker TEXT NOT NULL,
    kalshi_yes TEXT NOT NULL,
    kalshi_no TEXT NOT NULL,
    pm_condition TEXT NOT NULL,
    pm_token TEXT NOT NULL,
    pm_yes TEXT NOT NULL,
    pm_no TEXT NOT NULL,
    pm_yes_is_kalshi_yes INTEGER NOT NULL,
    created_ts REAL NOT NULL,
    UNIQUE(kalshi_ticker, pm_condition)
);
CREATE TABLE IF NOT EXISTS books (
    ts REAL NOT NULL,
    pair_id INTEGER NOT NULL,
    venue TEXT NOT NULL,          -- 'kalshi' | 'polymarket'
    bid REAL, ask REAL, bid_size REAL, ask_size REAL
);
CREATE INDEX IF NOT EXISTS books_pair_ts ON books(pair_id, venue, ts);
"""

PM_TAKER_RATE = 0.05          # documented sports fee: rate x p x (1-p)


def _norm(sport: str, name: str) -> str:
    if sport == "tennis":
        from sportsbot.data.tennis_data import normalize_player
        return normalize_player(name or "")
    from sportsbot.data.mlb_data import normalize_team
    return normalize_team(name or "")


@dataclass
class Pair:
    sport: str
    kalshi_ticker: str
    kalshi_yes: str
    kalshi_no: str
    pm_condition: str
    pm_token: str
    pm_yes: str
    pm_no: str
    pm_yes_is_kalshi_yes: bool


def match_pairs(sport: str, kalshi_markets, pm_markets, max_pairs: int = 40) -> list[Pair]:
    """Match open moneylines across venues by the set of competitor names.
    Both inputs are MarketInfo lists (home = the YES side on each venue).
    Polymarket candidates are taken highest-volume first."""
    by_names: dict[frozenset, list] = defaultdict(list)
    for k in kalshi_markets:
        if not k.home or not k.away:
            continue
        by_names[frozenset((_norm(sport, k.home), _norm(sport, k.away)))].append(k)
    out: list[Pair] = []
    used: set[str] = set()
    pms = sorted(pm_markets, key=lambda m: -float((m.meta or {}).get("volume24hr") or 0.0))
    for p in pms:
        key = frozenset((_norm(sport, p.home), _norm(sport, p.away)))
        if len(key) != 2:
            continue
        for k in by_names.get(key, []):
            if k.market_id in used:
                continue
            used.add(k.market_id)
            out.append(Pair(
                sport=sport, kalshi_ticker=k.market_id, kalshi_yes=k.home, kalshi_no=k.away,
                pm_condition=p.market_id, pm_token=p.yes_token_id, pm_yes=p.home, pm_no=p.away,
                pm_yes_is_kalshi_yes=(_norm(sport, p.home) == _norm(sport, k.home))))
            break
        if len(out) >= max_pairs:
            break
    return out


class TwoBookLogger:
    def __init__(self, db_path: str = "data/twobook.sqlite", kalshi=None, polymarket=None):
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)
        self.kalshi = kalshi
        self.polymarket = polymarket
        self._infos: dict[int, tuple] = {}      # pair_id -> (kalshi MarketInfo, pm MarketInfo)

    # ------------------------------------------------------------ discovery
    def discover(self, sports=("tennis", "mlb"), max_pairs: int = 40) -> int:
        from sportsbot.exchanges.kalshi import KalshiClient
        from sportsbot.exchanges.polymarket import PolymarketClient

        self.kalshi = self.kalshi or KalshiClient(env="prod")
        self.polymarket = self.polymarket or PolymarketClient()
        n = 0
        for sport in sports:
            try:
                km = self.kalshi.list_sports_markets(sport)
                pm = self.polymarket.list_sports_markets(sport)
            except Exception as exc:  # noqa: BLE001 — one venue down: log, keep the other sport
                log.warning("discovery failed for %s: %s", sport, exc)
                continue
            k_by = {m.market_id: m for m in km}
            p_by = {m.market_id: m for m in pm}
            for pr in match_pairs(sport, km, pm, max_pairs=max_pairs):
                self.conn.execute(
                    "INSERT OR IGNORE INTO pairs(sport,kalshi_ticker,kalshi_yes,kalshi_no,pm_condition,"
                    "pm_token,pm_yes,pm_no,pm_yes_is_kalshi_yes,created_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (pr.sport, pr.kalshi_ticker, pr.kalshi_yes, pr.kalshi_no, pr.pm_condition,
                     pr.pm_token, pr.pm_yes, pr.pm_no, int(pr.pm_yes_is_kalshi_yes), time.time()))
                row = self.conn.execute(
                    "SELECT pair_id FROM pairs WHERE kalshi_ticker=? AND pm_condition=?",
                    (pr.kalshi_ticker, pr.pm_condition)).fetchone()
                self._infos[row[0]] = (k_by[pr.kalshi_ticker], p_by[pr.pm_condition])
                n += 1
        self.conn.commit()
        return n

    # ------------------------------------------------------------ sampling
    def sample_once(self) -> int:
        """One pass over every active pair: both books, one timestamp each."""
        rows = []
        for pair_id, (km, pmm) in list(self._infos.items()):
            for venue, client, market in (("kalshi", self.kalshi, km), ("polymarket", self.polymarket, pmm)):
                try:
                    q = client.get_quote(market)
                except Exception as exc:  # noqa: BLE001 — a missing book is a missing row, not a crash
                    log.debug("quote failed %s %s: %s", venue, market.market_id, exc)
                    continue
                rows.append((time.time(), pair_id, venue, q.bid, q.ask,
                             q.bids[0].size if q.bids else None, q.asks[0].size if q.asks else None))
        self.conn.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def run(self, interval: float = 20.0, minutes: float = 30.0, rediscover_every: float = 1800.0,
            sports=("tennis", "mlb"), max_pairs: int = 40) -> dict:
        t_end = time.time() + minutes * 60
        last_disc = 0.0
        samples = passes = 0
        while time.time() < t_end:
            if time.time() - last_disc > rediscover_every:
                n = self.discover(sports=sports, max_pairs=max_pairs)
                log.info("twobook: %d pairs", n)
                last_disc = time.time()
            t0 = time.time()
            samples += self.sample_once()
            passes += 1
            time.sleep(max(0.0, interval - (time.time() - t0)))
        return {"pairs": len(self._infos), "passes": passes, "rows": samples}


# ---------------------------------------------------------------- analysis
def _grid(conn, pair_id: int, step: float) -> list[tuple]:
    """Align both venues on a `step`-second grid. Each grid point takes the
    latest sample at or before it, no older than 2*step. Returns
    (t, k_bid, k_ask, p_bid, p_ask) with Polymarket in Kalshi-YES orientation."""
    flip = not bool(conn.execute("SELECT pm_yes_is_kalshi_yes FROM pairs WHERE pair_id=?",
                                 (pair_id,)).fetchone()[0])
    series = {}
    for venue in ("kalshi", "polymarket"):
        series[venue] = conn.execute(
            "SELECT ts,bid,ask FROM books WHERE pair_id=? AND venue=? AND bid IS NOT NULL "
            "AND ask IS NOT NULL ORDER BY ts", (pair_id, venue)).fetchall()
    if not series["kalshi"] or not series["polymarket"]:
        return []
    t0 = max(series["kalshi"][0][0], series["polymarket"][0][0])
    t1 = min(series["kalshi"][-1][0], series["polymarket"][-1][0])
    out = []
    idx = {"kalshi": 0, "polymarket": 0}
    t = math.ceil(t0 / step) * step
    while t <= t1:
        vals = {}
        for venue, rows in series.items():
            i = idx[venue]
            while i + 1 < len(rows) and rows[i + 1][0] <= t:
                i += 1
            idx[venue] = i
            if rows[i][0] <= t and t - rows[i][0] <= 2 * step:
                vals[venue] = rows[i]
        if len(vals) == 2:
            _, kb, ka = vals["kalshi"]
            _, pb, pa = vals["polymarket"]
            if flip:
                pb, pa = 1.0 - pa, 1.0 - pb
            out.append((t, kb, ka, pb, pa))
        t += step
    return out


def _ols(x: list[float], y: list[float]) -> tuple[float, float]:
    n = len(x)
    mx, my = statistics.mean(x), statistics.mean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx <= 0 or n < 3:
        return float("nan"), float("nan")
    b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / sxx
    res = [c - my - b * (a - mx) for a, c in zip(x, y)]
    return b, math.sqrt(sum(r * r for r in res) / (n - 2) / sxx)


def _clustered(vals: list[float], groups: list[int]) -> tuple[float, float]:
    n = len(vals)
    m = statistics.mean(vals)
    by: dict[int, float] = defaultdict(float)
    for v, g in zip(vals, groups):
        by[g] += v - m
    return m, math.sqrt(sum(s * s for s in by.values())) / n


def report(db_path: str = "data/twobook.sqlite", step: float = 20.0,
           horizons=(1, 3, 6, 15), thetas=(0.01, 0.02, 0.03),
           kalshi_fee_multiplier: Optional[float] = None) -> dict:
    """Lead-lag and executable mark-out from the logged books."""
    from sportsbot.exchanges.kalshi import KNOWN_FEE_MULTIPLIERS, kalshi_taker_fee

    conn = sqlite3.connect(db_path)
    pairs = conn.execute("SELECT pair_id, sport, kalshi_ticker FROM pairs").fetchall()
    grids = {}
    for pid, sport, ticker in pairs:
        g = _grid(conn, pid, step)
        if len(g) >= max(horizons) + 5:
            grids[pid] = (sport, ticker, g)
    out: dict = {"pairs_logged": len(pairs), "pairs_usable": len(grids), "step": step,
                 "grid_points": sum(len(g) for _, _, g in grids.values()), "leadlag": {}, "markout": {}}
    if not grids:
        return out

    def mult(sport, ticker):
        if kalshi_fee_multiplier is not None:
            return kalshi_fee_multiplier
        series = ticker.split("-")[0]
        return KNOWN_FEE_MULTIPLIERS.get(series, 1.0)

    # ---- lead-lag: move of one venue's mid over k steps on the gap now
    for k in horizons:
        gap, dpm, dk = [], [], []
        for _, (sport, ticker, g) in grids.items():
            for i in range(len(g) - k):
                _, kb, ka, pb, pa = g[i]
                _, kb2, ka2, pb2, pa2 = g[i + k]
                km, pm = (kb + ka) / 2, (pb + pa) / 2
                gap.append(km - pm)
                dpm.append((pb2 + pa2) / 2 - pm)
                dk.append((kb2 + ka2) / 2 - km)
        if len(gap) > 10 and statistics.pstdev(gap) > 0:
            b1, s1 = _ols(gap, dpm)
            b2, s2 = _ols([-x for x in gap], dk)
            out["leadlag"][k] = {"n": len(gap), "pm_toward_kalshi": (b1, s1), "kalshi_toward_pm": (b2, s2),
                                 "gap_sd": statistics.pstdev(gap)}

    # ---- executable: hit the lagging ASK, mark to that venue's mid k steps on
    for k in horizons:
        for theta in thetas:
            cells = {"buy_pm": ([], []), "buy_kalshi": ([], [])}
            for pid, (sport, ticker, g) in grids.items():
                fm = mult(sport, ticker)
                for i in range(len(g) - k):
                    _, kb, ka, pb, pa = g[i]
                    _, kb2, ka2, pb2, pa2 = g[i + k]
                    km, pm = (kb + ka) / 2, (pb + pa) / 2
                    pm2, km2 = (pb2 + pa2) / 2, (kb2 + ka2) / 2
                    # Kalshi mid above Polymarket's ASK: buy YES on Polymarket
                    def add(cell, val):
                        cells[cell][0].append(val)
                        cells[cell][1].append(pid)
                    if km - pa >= theta and 0.02 < pa < 0.98:
                        add("buy_pm", pm2 - pa - PM_TAKER_RATE * pa * (1 - pa))
                    # Kalshi mid below Polymarket's BID: buy NO on Polymarket at 1-bid
                    if pb - km >= theta and 0.02 < pb < 0.98:
                        e = 1 - pb
                        add("buy_pm", (1 - pm2) - e - PM_TAKER_RATE * e * (1 - e))
                    # Polymarket mid above Kalshi's ASK: buy YES on Kalshi
                    if pm - ka >= theta and 0.02 < ka < 0.98:
                        add("buy_kalshi", km2 - ka - kalshi_taker_fee(ka, 1.0, fm))
                    if kb - pm >= theta and 0.02 < kb < 0.98:
                        e = 1 - kb
                        add("buy_kalshi", (1 - km2) - e - kalshi_taker_fee(e, 1.0, fm))
            for name, (vals, grp) in cells.items():
                if len(vals) >= 10:
                    m, se = _clustered(vals, grp)
                    out["markout"][(name, k, theta)] = {"n": len(vals), "pairs": len(set(grp)),
                                                        "mean": m, "se": se}
    return out


def format_report(r: dict) -> str:
    lines = [f"pairs logged {r['pairs_logged']}, usable {r['pairs_usable']}, grid points {r['grid_points']} "
             f"at {r['step']:.0f}s"]
    if r["leadlag"]:
        lines.append("lead-lag: fraction of the current gap each venue closes over k steps (± se)")
        for k, v in sorted(r["leadlag"].items()):
            b1, s1 = v["pm_toward_kalshi"]
            b2, s2 = v["kalshi_toward_pm"]
            lines.append(f"  k={k:>2} ({k * r['step']:>4.0f}s) n={v['n']:>6} gap sd {v['gap_sd']:.4f}  "
                         f"Polymarket->Kalshi {b1:+.3f}±{s1:.3f}   Kalshi->Polymarket {b2:+.3f}±{s2:.3f}")
    if r["markout"]:
        lines.append("executable: hit the lagging ask when the other mid is >= theta above it; "
                     "mark-out to that venue's mid after k steps, net taker fee, se clustered by pair")
        lines.append(f"  {'side':>10} {'k':>3} {'theta':>6} {'n':>6} {'pairs':>5} {'mean':>8} {'se':>7} {'t':>6}")
        for (name, k, theta), v in sorted(r["markout"].items()):
            t = v["mean"] / v["se"] if v["se"] else 0.0
            lines.append(f"  {name:>10} {k:>3} {theta:>6.2f} {v['n']:>6} {v['pairs']:>5} {v['mean']:>+8.4f} "
                         f"{v['se']:>7.4f} {t:>+6.2f}")
    return "\n".join(lines)
