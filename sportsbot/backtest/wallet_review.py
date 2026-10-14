"""Wallet review: grade one Polymarket wallet's trading history, and price
the exact opposite of every trade. READ-ONLY public data, no keys.

The user asked whether "the opposite of what I was doing" would have
worked. A trade and its mirror do not simply swap gains and losses: both
sides pay to cross the spread and both pay the taker fee. So the mirror of
a losing record is a winning one only where the original lost MORE than
those costs, i.e. where it was systematically on the wrong side (negative
mark-out), not merely paying the spread. This module measures exactly that.

For every TRADE row in `data-api.polymarket.com/activity?user=`:

* settlement P&L per share: BUY pays p and receives the outcome's payout v
  (1/0, or the split price); SELL receives p and gives up v;
* mark-out: the outcome's price from `clob /prices-history` 1 h and 24 h
  after the trade, signed so positive means the market moved the trader's
  way. For sports the horizon is capped at `gameStartTime`, so in-play
  prints never count; for everything else at the market's scheduled
  `endDate` (never `closedTime`, which conditions on the outcome);
* mirror P&L per share: the opposite position at the same moment, paying
  `mirror_cost` to cross (default 1c) plus the venue taker fee on that
  price: (p - v) - cost - fee for a BUY, mirrored for a SELL.

Results are broken down by category, entry price, side, lead time and
month, per trade and per dollar, with errors clustered by market.
Nothing here places orders or feeds the strategy.
"""

from __future__ import annotations

import json
import logging
import math
import re
import statistics
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

log = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
PAGE = 500
MAX_ROWS = 50_000
DEFAULT_FEE = 0.05         # Polymarket's sports/crypto taker rate when Gamma is silent

CATEGORIES = (
    ("crypto", ("bitcoin", "btc", "ethereum", "eth-", "solana", "xrp", "crypto", "up-or-down", "updown", "doge")),
    ("sports", ("nfl", "nba", "mlb", "nhl", "ncaa", "cfb", "epl", "ucl", "uefa", "premier-league", "laliga",
                "la-liga", "bundesliga", "serie-a", "ligue-1", "mls", "atp", "wta", "tennis", "ufc", "mma",
                "boxing", "f1", "formula-1", "nascar", "golf", "pga", "cricket", "ipl", "lol", "cs2", "csgo",
                "dota", "valorant", "esports", "world-cup", "super-bowl", "stanley-cup", "world-series",
                "-vs-", "match", "game")),
    ("economics", ("fed", "fomc", "rate", "cpi", "inflation", "gdp", "jobs", "unemployment", "recession",
                   "ecb", "boe", "boj", "treasury")),
    ("politics", ("election", "president", "trump", "biden", "harris", "senate", "house", "governor",
                  "democrat", "republican", "primary", "nominee", "vote", "poll", "mayor", "parliament",
                  "prime-minister", "minister", "cabinet")),
    ("weather", ("temperature", "weather", "highest-temp", "lowest-temp", "rain", "snow", "hurricane")),
)


def category(slug: str, title: str = "") -> str:
    text = f"{slug} {title}".lower().replace(" ", "-")
    for name, keys in CATEGORIES:
        if any(k in text for k in keys):
            return name
    return "other"


def price_bucket(p: float) -> str:
    for hi, label in ((0.1, "<0.10"), (0.3, "0.10-0.30"), (0.5, "0.30-0.50"), (0.7, "0.50-0.70"),
                      (0.9, "0.70-0.90")):
        if p < hi:
            return label
    return ">=0.90"


def lead_bucket(hours: Optional[float]) -> str:
    if hours is None:
        return "unknown"
    for hi, label in ((1, "<1h"), (6, "1-6h"), (24, "6-24h"), (168, "1-7d")):
        if hours < hi:
            return label
    return ">7d"


def _ts(s) -> Optional[float]:
    if not s:
        return None
    text = str(s).strip().replace("Z", "+00:00")
    if re.match(r"^\d{4}-\d{2}-\d{2} \d", text) and text.endswith("+00"):
        text += ":00"
    try:
        d = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def _list(v) -> list:
    if isinstance(v, list):
        return v
    try:
        out = json.loads(v or "[]")
        return out if isinstance(out, list) else []
    except (TypeError, ValueError):
        return []


@dataclass
class Market:
    condition_id: str
    slug: str
    closed: bool
    payouts: list[Optional[float]]          # per outcome index; None while unresolved
    tokens: list[str]
    end_ts: Optional[float]
    start_ts: Optional[float]               # gameStartTime, sports only
    fee_rate: float
    event_slug: str = ""

    @staticmethod
    def from_gamma(m: dict) -> "Market":
        prices = [float(x) for x in _list(m.get("outcomePrices")) if _num(x)]
        closed = bool(m.get("closed"))
        # resolved: closed with final prices summing to 1 that are a winner (1/0) or a 50-50 split
        resolved = (closed and len(prices) >= 2 and abs(sum(prices) - 1) < 1e-6
                    and (max(prices) >= 0.99 or all(p in (0.0, 0.5, 1.0) for p in prices)))
        payouts = [p if resolved else None for p in prices]
        sched = m.get("feeSchedule") or {}
        rate = float(sched.get("rate")) if m.get("feesEnabled") and _num(sched.get("rate")) else (
            0.0 if m.get("feesEnabled") is False else DEFAULT_FEE)
        ev = (m.get("events") or [{}])[0] or {}
        return Market(condition_id=str(m.get("conditionId")), slug=str(m.get("slug") or ""),
                      closed=closed, payouts=payouts, tokens=[str(t) for t in _list(m.get("clobTokenIds"))],
                      end_ts=_ts(m.get("endDate")), start_ts=_ts(m.get("gameStartTime")),
                      fee_rate=rate, event_slug=str(ev.get("slug") or ""))


def _num(x) -> bool:
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False


@dataclass
class Graded:
    ts: float
    condition_id: str
    slug: str
    title: str
    side: str                 # BUY | SELL
    outcome_index: int
    price: float
    shares: float
    usdc: float
    category: str
    lead_h: Optional[float]
    payout: Optional[float] = None
    pnl_share: Optional[float] = None      # settlement P&L per share, before any fee
    markout_1h: Optional[float] = None
    markout_24h: Optional[float] = None
    mirror_share: Optional[float] = None   # opposite trade, after crossing cost and fee
    extra: dict = field(default_factory=dict)


class WalletReview:
    def __init__(self, address: str, http=None, pause: float = 0.15, sleep=time.sleep):
        if not ADDRESS_RE.match(address or ""):
            raise ValueError("a Polymarket wallet address is 0x followed by 40 hex characters")
        self.address = address.lower()
        if http is None:
            import httpx
            http = httpx.Client(timeout=30, headers={"User-Agent": "sportsbot/1.0"})
        self.http = http
        self.pause = pause
        self.sleep = sleep

    def _get(self, url: str, params=None):
        for wait in (1.0, 2.0, 4.0, None):
            r = self.http.get(url, params=params)
            if r.status_code == 429 and wait is not None:
                self.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        return None

    # -------------------------------------------------------------- fetch
    def activity(self) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        while offset < MAX_ROWS:
            page = self._get(f"{DATA_API}/activity", {"user": self.address, "limit": PAGE,
                                                       "offset": offset}) or []
            rows.extend(page)
            if len(page) < PAGE:
                break
            offset += PAGE
            self.sleep(self.pause)
        return rows

    def markets(self, condition_ids: Iterable[str]) -> dict[str, Market]:
        ids = sorted(set(condition_ids))
        out: dict[str, Market] = {}
        for i in range(0, len(ids), 20):
            chunk = ids[i:i + 20]
            for closed in ("true", "false"):
                params = [("condition_ids", c) for c in chunk] + [("closed", closed), ("limit", "100")]
                for m in self._get(f"{GAMMA}/markets", params) or []:
                    mk = Market.from_gamma(m)
                    out[mk.condition_id] = mk
                self.sleep(self.pause)
        return out

    def history(self, token: str, start: float, end: float) -> list[tuple[float, float]]:
        data = self._get(f"{CLOB}/prices-history", {"market": token, "startTs": int(start),
                                                     "endTs": int(end), "fidelity": 10}) or {}
        self.sleep(self.pause)
        return sorted((float(h["t"]), float(h["p"])) for h in data.get("history") or []
                      if _num(h.get("t")) and _num(h.get("p")))

    # -------------------------------------------------------------- grade
    def grade(self, rows: list[dict], markets: dict[str, Market], mirror_cost: float = 0.01,
              with_history: bool = True) -> list[Graded]:
        out = []
        hist_cache: dict[str, list] = {}
        first_ts: dict[str, float] = {}           # each token's earliest trade: one history fetch covers all
        for r in rows:
            mk = markets.get(str(r.get("conditionId")))
            idx = int(r.get("outcomeIndex") or 0)
            if r.get("type") == "TRADE" and mk and idx < len(mk.tokens) and _num(r.get("timestamp")):
                tok = mk.tokens[idx]
                first_ts[tok] = min(first_ts.get(tok, math.inf), float(r["timestamp"]))
        for r in rows:
            if r.get("type") != "TRADE" or str(r.get("side")).upper() not in ("BUY", "SELL"):
                continue
            if not (_num(r.get("price")) and _num(r.get("size"))):
                continue
            cid = str(r.get("conditionId"))
            mk = markets.get(cid)
            ts, p = float(r["timestamp"]), float(r["price"])
            idx = int(r.get("outcomeIndex") or 0)
            horizon_end = (mk.start_ts or mk.end_ts) if mk else None
            g = Graded(ts=ts, condition_id=cid, slug=str(r.get("slug") or ""), title=str(r.get("title") or ""),
                       side=str(r["side"]).upper(), outcome_index=idx, price=p, shares=float(r["size"]),
                       usdc=float(r.get("usdcSize") or p * float(r["size"])),
                       category=category(str(r.get("eventSlug") or r.get("slug") or ""), str(r.get("title") or "")),
                       lead_h=(horizon_end - ts) / 3600 if horizon_end and horizon_end > ts else None)
            sign = 1.0 if g.side == "BUY" else -1.0
            if mk and idx < len(mk.payouts) and mk.payouts[idx] is not None:
                v = mk.payouts[idx]
                g.payout = v
                g.pnl_share = sign * (v - p)
                q = 1.0 - p                                   # the complement's price
                fee = mk.fee_rate * q * (1 - q)
                g.mirror_share = -g.pnl_share - mirror_cost - fee
            if with_history and mk and idx < len(mk.tokens):
                tok = mk.tokens[idx]
                if tok not in hist_cache:
                    try:
                        lo = first_ts.get(tok, ts)
                        hi = max(lo + 2 * 86400, horizon_end or 0, ts + 2 * 86400)
                        hist_cache[tok] = self.history(tok, lo - 3600, min(hi, time.time()))
                    except Exception as exc:  # noqa: BLE001 — no history: no mark-out, not a crash
                        log.debug("history failed %s: %s", tok[:10], exc)
                        hist_cache[tok] = []
                hist = hist_cache[tok]
                for hours, attr in ((1, "markout_1h"), (24, "markout_24h")):
                    t = ts + hours * 3600
                    if horizon_end is not None and t > horizon_end:
                        t = horizon_end
                    if t <= ts:
                        continue
                    px = _at_or_before(hist, t, after=ts)
                    if px is not None:
                        setattr(g, attr, sign * (px - p))
            out.append(g)
        return out

    def run(self, mirror_cost: float = 0.01, with_history: bool = True) -> list[Graded]:
        rows = self.activity()
        mk = self.markets(r.get("conditionId") for r in rows if r.get("type") == "TRADE")
        return self.grade(rows, mk, mirror_cost=mirror_cost, with_history=with_history)


def _at_or_before(hist: list[tuple[float, float]], t: float, after: float) -> Optional[float]:
    best = None
    for ts, p in hist:
        if ts > t:
            break
        if ts > after:
            best = p
    return best


# ------------------------------------------------------------------ report
def _mean_se(vals: list[float], groups: list[str]) -> tuple[Optional[float], Optional[float]]:
    if not vals:
        return None, None
    m = statistics.mean(vals)
    if len(vals) < 2:
        return m, None
    by: dict[str, float] = defaultdict(float)
    for v, g in zip(vals, groups):
        by[g] += v - m
    return m, math.sqrt(sum(x * x for x in by.values())) / len(vals)


def summarize(graded: list[Graded]) -> dict:
    def block(rows: list[Graded]) -> dict:
        settled = [g for g in rows if g.pnl_share is not None]
        spent = sum(g.usdc for g in settled if g.side == "BUY")
        pnl = sum(g.pnl_share * g.shares for g in settled)
        mirror = sum(g.mirror_share * g.shares for g in settled)
        out = {"trades": len(rows), "markets": len({g.condition_id for g in rows}),
               "settled": len(settled), "usdc_bought": round(spent, 2),
               "pnl_usd": round(pnl, 2), "mirror_pnl_usd": round(mirror, 2)}
        for key, attr in (("pnl_share", "pnl_share"), ("mirror_share", "mirror_share"),
                          ("markout_1h", "markout_1h"), ("markout_24h", "markout_24h")):
            vals = [(getattr(g, attr), g.condition_id) for g in rows if getattr(g, attr) is not None]
            m, se = _mean_se([v for v, _ in vals], [c for _, c in vals])
            out[key] = {"n": len(vals), "mean": m, "se": se}
        return out

    res = {"all": block(graded), "by": {}}
    for dim, fn in (("category", lambda g: g.category), ("price", lambda g: price_bucket(g.price)),
                    ("side", lambda g: g.side), ("lead", lambda g: lead_bucket(g.lead_h)),
                    ("month", lambda g: datetime.fromtimestamp(g.ts, timezone.utc).strftime("%Y-%m"))):
        groups: dict[str, list[Graded]] = defaultdict(list)
        for g in graded:
            groups[fn(g)].append(g)
        res["by"][dim] = {k: block(v) for k, v in sorted(groups.items())}
    return res


def format_summary(res: dict) -> str:
    def cell(d):
        if d["mean"] is None:
            return "—"
        se = f" ± {d['se'] * 100:.1f}" if d["se"] is not None else ""
        return f"{d['mean'] * 100:+.1f}{se} ({d['n']})"

    head = (f"{'':22s} {'trades':>6s} {'mkts':>5s} {'$ bought':>10s} {'P&L $':>9s} {'mirror $':>9s}  "
            f"{'P&L/share pt':>18s}  {'mirror/share pt':>18s}  {'mark-out 1h pt':>16s}  {'mark-out 24h pt':>16s}")

    def line(name, b):
        return (f"{name:22s} {b['trades']:6d} {b['markets']:5d} {b['usdc_bought']:10,.0f} {b['pnl_usd']:9,.0f} "
                f"{b['mirror_pnl_usd']:9,.0f}  {cell(b['pnl_share']):>18s}  {cell(b['mirror_share']):>18s}  "
                f"{cell(b['markout_1h']):>16s}  {cell(b['markout_24h']):>16s}")

    lines = [head, line("ALL", res["all"])]
    for dim, groups in res["by"].items():
        lines.append(f"-- by {dim}")
        for k, b in groups.items():
            lines.append(line(f"  {k}", b))
    return "\n".join(lines)


def to_json(graded: list[Graded]) -> list[dict]:
    return [asdict(g) for g in graded]
