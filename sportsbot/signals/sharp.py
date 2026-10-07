"""Sharp-line CLV harness: grade every decision against Pinnacle's close.

Why this exists. Every edge that has survived fees in the literature was
measured as closing-line value against a SHARP book, not as PnL and not
against the venue's own last print (`reports/Beating prediction market
prices.md`, rank 1). The venue close is contaminated two ways this repo
has already documented: in-play prints leak the result, and the home-side
drift fakes positive 24-hour CLV. Pinnacle's pre-start line is the
benchmark the bot has to beat, so this module:

1. snapshots sportsbook h2h lines from The Odds API on a budgeted
   cadence, de-vigs each book with Shin (`core.odds.shin_devig`), and
   stores the result (`sharp_quotes`);
2. remembers who each venue market is between (`market_meta`) so a
   decision can be matched to a sportsbook event after delisting;
3. grades every scanner decision -- taken or skipped -- and every settled
   bet against the last sharp quote at or before ``commence - min_lead``,
   in probability space, net of the venue fee at that price;
4. reports mean CLV with an event-clustered bootstrap CI, the venue-vs-
   sharp gap distribution, and the beta of the sharp close on model
   disagreement -- plus a PASS / FAIL / INSUFFICIENT verdict against the
   pre-registered criterion (>= 1,000 graded decisions, mean net CLV > 0
   with the 95% CI excluding zero).

Data only. Nothing here places orders. The one trading-path effect is
tighten-only: when ``sharp.enforce_adaptive`` is on, the adaptive layer's
rolling CLV uses the sharp close where one exists, so a sport that is
negative against Pinnacle raises its own bar and cuts its own cap
(`bot/positions.adaptive_overrides`), which can only ever reduce risk.

Budget. The Odds API meters CREDITS (not requests): one h2h call for one
sport with one bookmaker group costs 1 credit; the free tier is 500 a
month, paid tiers 20k-15M. `/v4/sports` is free. Snapshots stop when the
reported remaining credits fall below ``credits_reserve``.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

import httpx

from sportsbot.core.odds import shin_devig
from sportsbot.core.types import Sport
from sportsbot.data.store import Store

log = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_API_KEY_ENV = "ODDS_API_KEY"

# Static sport keys. Tennis keys are per-tournament (tennis_atp_us_open,
# tennis_wta_wuhan, ...) and change weekly, so they are discovered from
# /v4/sports instead. Table tennis has no sharp line anywhere (the fast
# leagues are priced by their own operator), so it is deliberately absent.
STATIC_SPORT_KEYS: dict[Sport, tuple[str, ...]] = {
    Sport.BASEBALL: ("baseball_mlb",),
    Sport.TENNIS: (),
    Sport.TABLE_TENNIS: (),
}

# Pre-registered pass criterion (reports/Beating prediction market prices.md).
MIN_GRADED_FOR_VERDICT = 1000

KV_LAST_SNAPSHOT = "sharp:last_snapshot_ts"
KV_CREDITS = "sharp:credits_remaining"
KV_TENNIS_KEYS = "sharp:tennis_keys"
KV_BUDGET_WARNED = "sharp:budget_warned_on"


@dataclass
class SharpConfig:
    enabled: bool = True
    bookmakers: tuple[str, ...] = ("pinnacle",)
    sharp_book: str = "pinnacle"           # the one decisions are graded against
    snapshot_interval_minutes: float = 15.0
    min_lead_minutes: float = 10.0         # close = last quote at/before start - this
    credits_reserve: int = 50              # stop snapshotting below this
    match_threshold: float = 0.85
    time_slack_hours: float = 6.0          # venue start vs book commence_time
    enforce_adaptive: bool = True          # feed sharp CLV to the tighten-only layer

    @classmethod
    def from_cfg(cls, cfg: dict) -> "SharpConfig":
        s = cfg.get("sharp", {}) or {}
        books = s.get("bookmakers", ["pinnacle"])
        if isinstance(books, str):
            books = [b.strip() for b in books.split(",") if b.strip()]
        return cls(
            enabled=bool(s.get("enabled", True)),
            bookmakers=tuple(books) or ("pinnacle",),
            sharp_book=str(s.get("sharp_book", "pinnacle")),
            snapshot_interval_minutes=float(s.get("snapshot_interval_minutes", 15.0)),
            min_lead_minutes=float(s.get("min_lead_minutes", 10.0)),
            credits_reserve=int(s.get("credits_reserve", 50)),
            match_threshold=float(s.get("match_threshold", 0.85)),
            time_slack_hours=float(s.get("time_slack_hours", 6.0)),
            enforce_adaptive=bool(s.get("enforce_adaptive", True)),
        )


# ---------------------------------------------------------------------------
# The Odds API
# ---------------------------------------------------------------------------
@dataclass
class SharpEvent:
    event_id: str
    sport_key: str
    commence_time: datetime
    home_team: str
    away_team: str
    # bookmaker -> (home_implied, away_implied), vig included
    books: dict[str, tuple[float, float]] = field(default_factory=dict)


def _parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_events(payload: list[dict]) -> list[SharpEvent]:
    """Odds API v4 `/odds` payload -> two-way h2h events per bookmaker.

    Only books quoting exactly the two participants (no draw) are kept:
    the harness grades binary moneylines, and a three-way price de-vigs
    to a different quantity.
    """
    out: list[SharpEvent] = []
    for g in payload or []:
        try:
            home, away = str(g["home_team"]), str(g["away_team"])
            ev = SharpEvent(event_id=str(g["id"]), sport_key=str(g.get("sport_key", "")),
                            commence_time=_parse_ts(g["commence_time"]),
                            home_team=home, away_team=away)
        except (KeyError, ValueError, TypeError):
            continue
        for bk in g.get("bookmakers", []) or []:
            key = bk.get("key")
            for mkt in bk.get("markets", []) or []:
                if mkt.get("key") != "h2h":
                    continue
                prices = {}
                for o in mkt.get("outcomes", []) or []:
                    try:
                        prices[str(o["name"])] = float(o["price"])
                    except (KeyError, ValueError, TypeError):
                        pass
                if set(prices) != {home, away} or home == away:
                    continue
                if prices[home] <= 1.0 or prices[away] <= 1.0:
                    continue
                ev.books[str(key)] = (1.0 / prices[home], 1.0 / prices[away])
        if ev.books:
            out.append(ev)
    return out


def quote_rows(events: Iterable[SharpEvent]) -> list[dict]:
    """Flatten parsed events into `sharp_quotes` rows with Shin fair probs."""
    rows = []
    for ev in events:
        for book, (hi, ai) in ev.books.items():
            fair = shin_devig([hi, ai])
            rows.append({
                "sport_key": ev.sport_key, "event_id": ev.event_id,
                "commence_time": ev.commence_time.isoformat(),
                "home_team": ev.home_team, "away_team": ev.away_team,
                "bookmaker": book,
                "home_implied": hi, "away_implied": ai,
                "home_fair": fair[0], "away_fair": fair[1],
                "overround": hi + ai - 1.0,
            })
    return rows


class OddsApiClient:
    """Thin client; every `/odds` call costs credits, so callers budget."""

    def __init__(self, api_key: str, http: Optional[httpx.Client] = None,
                 base: str = ODDS_API_BASE) -> None:
        self.api_key = api_key
        self.base = base
        self.http = http or httpx.Client(timeout=30.0)
        self.credits_remaining: Optional[int] = None
        self.credits_used_last: Optional[int] = None

    def _record_credits(self, r: httpx.Response) -> None:
        rem = r.headers.get("x-requests-remaining")
        last = r.headers.get("x-requests-last")
        try:
            self.credits_remaining = int(float(rem)) if rem is not None else None
        except ValueError:
            self.credits_remaining = None
        try:
            self.credits_used_last = int(float(last)) if last is not None else None
        except ValueError:
            self.credits_used_last = None

    def sports(self) -> list[dict]:
        """Active sport keys. Free: does not consume credits."""
        r = self.http.get(f"{self.base}/sports", params={"apiKey": self.api_key})
        r.raise_for_status()
        self._record_credits(r)
        return list(r.json() or [])

    def tennis_keys(self) -> list[str]:
        return sorted(s["key"] for s in self.sports()
                      if str(s.get("key", "")).startswith("tennis_")
                      and s.get("active", True) and not s.get("has_outrights"))

    def h2h(self, sport_key: str, bookmakers: Iterable[str]) -> list[SharpEvent]:
        """One credit per call (per 10 bookmakers). Raises on HTTP errors so
        the collector can decide whether to keep going."""
        r = self.http.get(
            f"{self.base}/sports/{sport_key}/odds",
            params={"apiKey": self.api_key, "markets": "h2h",
                    "oddsFormat": "decimal",
                    "bookmakers": ",".join(bookmakers)})
        self._record_credits(r)
        if r.status_code == 404:      # key expired between /sports and now
            return []
        r.raise_for_status()
        return parse_events(r.json())


# ---------------------------------------------------------------------------
# Collector (runs inside the runner cycle or from the CLI)
# ---------------------------------------------------------------------------
class SharpCollector:
    def __init__(self, store: Store, cfg: SharpConfig,
                 client: Optional[OddsApiClient] = None,
                 now: Optional[Callable[[], datetime]] = None) -> None:
        self.store = store
        self.cfg = cfg
        self.client = client
        self.now = now or (lambda: datetime.now(timezone.utc))

    # -- bookkeeping ------------------------------------------------------
    def record_markets(self, markets: Iterable) -> int:
        n = 0
        for m in markets:
            if not m.home or not m.away:
                continue
            self.store.record_market(
                m.market_id, m.exchange.value if m.exchange else None,
                m.sport.value if m.sport else None, m.home, m.away, m.start_time)
            n += 1
        return n

    def due(self) -> bool:
        last = self.store.get_kv(KV_LAST_SNAPSHOT)
        if not last:
            return True
        try:
            age = self.now() - _parse_ts(last)
        except ValueError:
            return True
        return age >= timedelta(minutes=self.cfg.snapshot_interval_minutes)

    def budget_ok(self) -> bool:
        rem = self.store.get_kv(KV_CREDITS)
        if rem is None:
            return True
        if int(rem) >= self.cfg.credits_reserve:
            return True
        today = self.now().date().isoformat()
        if self.store.get_kv(KV_BUDGET_WARNED) != today:
            log.warning("sharp: %s credits left on The Odds API, below the "
                        "reserve of %d; snapshots paused", rem,
                        self.cfg.credits_reserve)
            self.store.set_kv(KV_BUDGET_WARNED, today)
        return False

    def sport_keys(self, sports: Iterable[Sport]) -> list[str]:
        keys: list[str] = []
        for sport in sports:
            keys.extend(STATIC_SPORT_KEYS.get(sport, ()))
            if sport is Sport.TENNIS and self.client is not None:
                keys.extend(self._tennis_keys())
        return sorted(set(keys))

    def _tennis_keys(self) -> list[str]:
        today = self.now().date().isoformat()
        cached = self.store.get_kv(KV_TENNIS_KEYS) or {}
        if cached.get("date") == today:
            return list(cached.get("keys", []))
        try:
            keys = self.client.tennis_keys()
        except Exception:
            log.exception("sharp: /sports lookup failed; reusing cached tennis keys")
            return list(cached.get("keys", []))
        self.store.set_kv(KV_TENNIS_KEYS, {"date": today, "keys": keys})
        return keys

    # -- the snapshot -----------------------------------------------------
    def snapshot(self, sports: Iterable[Sport], force: bool = False) -> dict:
        """Fetch and store one snapshot for `sports`. Never raises."""
        summary = {"calls": 0, "events": 0, "rows": 0, "credits": None,
                   "skipped": None}
        if self.client is None:
            summary["skipped"] = "no api key"
            return summary
        if not force and not self.due():
            summary["skipped"] = "not due"
            return summary
        if not self.budget_ok():
            summary["skipped"] = "budget"
            return summary
        keys = self.sport_keys(sports)
        if not keys:
            summary["skipped"] = "no sport keys"
            return summary
        ts = self.now().isoformat()
        rows: list[dict] = []
        attempted = 0
        for key in keys:
            attempted += 1
            try:
                events = self.client.h2h(key, self.cfg.bookmakers)
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                log.warning("sharp: %s returned %s", key, code)
                if code in (401, 429):
                    break           # bad key or out of credits: stop spending
                continue
            except Exception:
                log.exception("sharp: fetch failed for %s", key)
                continue
            summary["calls"] += 1
            summary["events"] += len(events)
            rows.extend(quote_rows(events))
        if rows:
            summary["rows"] = self.store.record_sharp_quotes(rows, ts=ts)
        if attempted:
            # A failed attempt still consumed the slot: retrying every
            # cycle after a 401/429 would spend requests on a dead key.
            self.store.set_kv(KV_LAST_SNAPSHOT, ts)
        if self.client.credits_remaining is not None:
            self.store.set_kv(KV_CREDITS, self.client.credits_remaining)
            summary["credits"] = self.client.credits_remaining
        return summary


# ---------------------------------------------------------------------------
# Matching venue markets to sportsbook events
# ---------------------------------------------------------------------------
def match_event(meta: dict, events: list[dict], threshold: float = 0.85,
                slack_hours: float = 6.0) -> Optional[tuple[dict, bool]]:
    """The sportsbook event a venue market is about, and whether the venue's
    YES side (`meta['home']`) is the book's `home_team`.

    Conservative, like the entity matcher: both participants must clear
    `threshold` in one orientation, start times must agree within
    `slack_hours` when both are known, and a second event scoring within
    0.05 of the best is an ambiguity -> no match.
    """
    # Imported here: `sportsbot.bot` re-exports the runner, which imports
    # this module, and the matcher is a pure helper with no bot state.
    from sportsbot.bot.matching import similarity

    home, away = meta.get("home") or "", meta.get("away") or ""
    if not home or not away:
        return None
    start = meta.get("start_time")
    start_dt = _parse_ts(start) if isinstance(start, str) and start else (
        start if isinstance(start, datetime) else None)
    scored: list[tuple[float, dict, bool]] = []
    for ev in events:
        if start_dt is not None:
            try:
                gap = abs(_parse_ts(ev["commence_time"]) - start_dt)
            except (ValueError, KeyError):
                gap = timedelta(0)
            if gap > timedelta(hours=slack_hours):
                continue
        straight = min(similarity(home, ev["home_team"]),
                       similarity(away, ev["away_team"]))
        flipped = min(similarity(home, ev["away_team"]),
                      similarity(away, ev["home_team"]))
        if straight >= threshold or flipped >= threshold:
            if straight >= flipped:
                scored.append((straight, ev, True))
            else:
                scored.append((flipped, ev, False))
    if not scored:
        return None
    scored.sort(key=lambda t: -t[0])
    if len(scored) > 1 and scored[1][0] > scored[0][0] - 0.05 \
            and scored[1][1]["event_id"] != scored[0][1]["event_id"]:
        return None
    _, ev, yes_is_home = scored[0]
    return ev, yes_is_home


def quote_at(quotes: list[dict], at: datetime) -> Optional[dict]:
    """Last quote with ts <= `at` (quotes are ordered by ts)."""
    best = None
    for q in quotes:
        if _parse_ts(q["ts"]) <= at:
            best = q
        else:
            break
    return best


def sharp_close(quotes: list[dict], commence_time: datetime,
                min_lead_minutes: float) -> Optional[dict]:
    """The closing sharp quote: last one at or before start - min_lead.

    Anything later is refused rather than used -- a quote inside the lead
    window may already be in-play, and a close that knows the score is the
    exact contamination the venue close suffers from.
    """
    return quote_at(quotes, commence_time - timedelta(minutes=min_lead_minutes))


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------
FeeFor = Callable[[str, str], Callable[[float], float]]
"""(exchange, market_id) -> (price -> marginal fee per share)."""


def default_fee_for(exchange: str, market_id: str) -> Callable[[float], float]:
    """Venue taker fee at a price -- the conservative cost of a decision."""
    if (exchange or "").lower() == "kalshi":
        from sportsbot.exchanges.kalshi import (
            kalshi_fee_multiplier,
            kalshi_fee_per_share,
        )

        mult = kalshi_fee_multiplier(market_id)
        return lambda p: kalshi_fee_per_share(p, mult)
    from sportsbot.exchanges.polymarket import taker_fee

    return lambda p: taker_fee(p, 1.0)


@dataclass
class Graded:
    """One decision or bet scored against the sharp close, side frame."""

    kind: str                  # 'bet' | 'skip' | 'settled'
    ref_id: int
    ts: str
    sport: str
    market_id: str
    event_id: str
    side: str                  # yes | no (skips: the model-preferred side)
    price: float               # paid (bets) or venue mid (skips)
    venue_mid_yes: float
    model_yes: Optional[float]
    sharp_close_yes: float
    sharp_then_yes: Optional[float]   # sharp quote at decision time
    fee: float
    stake: Optional[float] = None
    pnl: Optional[float] = None
    outcome: Optional[int] = None

    @property
    def sharp_close_side(self) -> float:
        return self.sharp_close_yes if self.side == "yes" else 1.0 - self.sharp_close_yes

    @property
    def clv_gross(self) -> float:
        return self.sharp_close_side - self.price

    @property
    def clv_net(self) -> float:
        return self.clv_gross - self.fee

    @property
    def cluster(self) -> str:
        return self.event_id


class Grader:
    def __init__(self, store: Store, cfg: SharpConfig,
                 fee_for: FeeFor = default_fee_for) -> None:
        self.store = store
        self.cfg = cfg
        self.fee_for = fee_for
        self._events: Optional[list[dict]] = None
        self._quotes: dict[str, list[dict]] = {}
        self._closes: dict[str, Optional[dict]] = {}
        # market_id -> (meta, event, yes_is_home) or None. Decisions repeat
        # the same market every cycle, so matching once per market turns
        # thousands of fuzzy comparisons into a few hundred.
        self._matched: dict[str, Optional[tuple[dict, dict, bool]]] = {}

    # -- lookups ----------------------------------------------------------
    def events(self) -> list[dict]:
        if self._events is None:
            self._events = self.store.sharp_events(self.cfg.sharp_book)
        return self._events

    def quotes(self, event_id: str) -> list[dict]:
        if event_id not in self._quotes:
            self._quotes[event_id] = self.store.sharp_quotes_for(
                event_id, self.cfg.sharp_book)
        return self._quotes[event_id]

    def close(self, ev: dict) -> Optional[dict]:
        eid = ev["event_id"]
        if eid not in self._closes:
            self._closes[eid] = sharp_close(
                self.quotes(eid), _parse_ts(ev["commence_time"]),
                self.cfg.min_lead_minutes)
        return self._closes[eid]

    def match(self, market_id: str) -> Optional[tuple[dict, dict, bool]]:
        """(meta, event, yes_is_home) for a venue market, cached."""
        if market_id not in self._matched:
            meta = self.store.market_meta(market_id)
            hit = None
            if meta is not None:
                hit = match_event(meta, self.events(), self.cfg.match_threshold,
                                  self.cfg.time_slack_hours)
            self._matched[market_id] = ((meta, hit[0], hit[1]) if hit else
                                        (meta, None, False) if meta else None)
        return self._matched[market_id]

    def resolve(self, market_id: str) -> Optional[tuple[dict, dict, bool, dict]]:
        """(meta, event, yes_is_home, close) for a venue market, if gradeable."""
        m = self.match(market_id)
        if m is None or m[1] is None:
            return None
        meta, ev, yes_is_home = m
        close = self.close(ev)
        if close is None:
            return None
        return meta, ev, yes_is_home, close

    @staticmethod
    def _venue(meta: dict, fallback: str | None) -> str:
        """Paper fills record exchange='paper'; the fee belongs to the venue
        whose book was quoted, which the metadata remembers."""
        ex = (fallback or "").lower()
        if ex in ("", "paper"):
            ex = (meta.get("exchange") or "").lower()
        return ex

    @staticmethod
    def _yes_prob(q: dict, yes_is_home: bool) -> float:
        return q["home_fair"] if yes_is_home else q["away_fair"]

    # -- grading ----------------------------------------------------------
    def grade_decisions(self, account: str, since_ts: str | None = None
                        ) -> tuple[list[Graded], dict]:
        """Every bet/skip decision with a price, scored at the sharp close.

        Skips are scored as if the model-preferred side had been bought at
        the venue mid: that is the decision the scanner declined, and the
        number says whether declining was right against Pinnacle.
        """
        out: list[Graded] = []
        funnel = {"decisions": 0, "no_meta": 0, "no_event": 0, "no_close": 0,
                  "graded": 0}
        for d in self.store.decisions_since(account, since_ts):
            funnel["decisions"] += 1
            m = self.match(d["market_id"])
            if m is None:
                funnel["no_meta"] += 1
                continue
            meta, ev, yes_is_home = m
            if ev is None:
                funnel["no_event"] += 1
                continue
            close = self.close(ev)
            if close is None:
                funnel["no_close"] += 1
                continue
            mid = d.get("market_prob")
            if mid is None or not (0.0 < mid < 1.0):
                continue
            model = d.get("model_prob")
            if d["action"] == "bet" and d.get("side") and d.get("price"):
                side, price = d["side"], float(d["price"])
            else:
                if model is None:
                    continue
                side = "yes" if model >= mid else "no"
                price = mid if side == "yes" else 1.0 - mid
            then = quote_at(self.quotes(ev["event_id"]), _parse_ts(d["ts"]))
            fee = self.fee_for(self._venue(meta, None), d["market_id"])(price)
            out.append(Graded(
                kind="bet" if d["action"] == "bet" else "skip",
                ref_id=int(d["id"]), ts=d["ts"], sport=d.get("sport") or "unknown",
                market_id=d["market_id"], event_id=ev["event_id"], side=side,
                price=price, venue_mid_yes=float(mid), model_yes=model,
                sharp_close_yes=self._yes_prob(close, yes_is_home),
                sharp_then_yes=(self._yes_prob(then, yes_is_home) if then else None),
                fee=fee, stake=d.get("stake")))
            funnel["graded"] += 1
        return out, funnel

    def grade_bets(self, mode: str | None = None, write: bool = True,
                   only_missing: bool = False) -> list[Graded]:
        """Settled and open bets at their fill price. With `write`, the
        sharp close is persisted on the row (`bets.sharp_closing_price`, in
        the side frame, matching `closing_price`) so the adaptive layer and
        `status` can read it without re-matching. `only_missing` restricts
        the pass to rows without one (the per-cycle backfill)."""
        out: list[Graded] = []
        for b in self.store.all_bets(mode):
            if only_missing and b.get("sharp_closing_price") is not None:
                continue
            res = self.resolve(b["market_id"])
            if res is None:
                continue
            meta, ev, yes_is_home, close = res
            close_yes = self._yes_prob(close, yes_is_home)
            side = b["side"]
            close_side = close_yes if side == "yes" else 1.0 - close_yes
            if write and b.get("sharp_closing_price") != close_side:
                self.store.set_sharp_close(int(b["id"]), close_side)
            then = quote_at(self.quotes(ev["event_id"]), _parse_ts(b["ts"]))
            fee = self.fee_for(self._venue(meta, b.get("exchange")),
                               b["market_id"])(float(b["entry_price"]))
            entry_yes = (float(b["entry_price"]) if side == "yes"
                         else 1.0 - float(b["entry_price"]))
            out.append(Graded(
                kind="settled" if b.get("outcome") is not None else "bet",
                ref_id=int(b["id"]), ts=b["ts"], sport=b.get("sport") or "unknown",
                market_id=b["market_id"], event_id=ev["event_id"], side=side,
                price=float(b["entry_price"]), venue_mid_yes=entry_yes,
                model_yes=(b["model_prob"] if side == "yes"
                           else (1.0 - b["model_prob"]) if b.get("model_prob") is not None
                           else None),
                sharp_close_yes=close_yes,
                sharp_then_yes=(self._yes_prob(then, yes_is_home) if then else None),
                fee=fee, stake=b.get("stake"), pnl=b.get("pnl"),
                outcome=b.get("outcome")))
        return out


def with_sharp_closes(rows: list[dict]) -> list[dict]:
    """Settled-bet rows with `closing_price` replaced by the sharp close
    where one was recorded. Feeds `adaptive_overrides`, which is tighten-
    only, so swapping the benchmark can only make a sport stricter against
    Pinnacle, never looser than its configured base."""
    out = []
    for r in rows:
        sharp = r.get("sharp_closing_price")
        if sharp is not None:
            r = dict(r)
            r["closing_price"] = sharp
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def cluster_bootstrap_mean(values: list[float], clusters: list[str],
                           n_boot: int = 2000, seed: int = 0,
                           alpha: float = 0.05) -> Optional[dict]:
    """Mean with a percentile CI from resampling CLUSTERS (events), not
    rows: both sides of one game, or several fills on one market, are one
    draw of the same information."""
    n = len(values)
    if n == 0 or n != len(clusters):
        return None
    groups: dict[str, list[float]] = {}
    for v, c in zip(values, clusters):
        groups.setdefault(c, []).append(v)
    keys = list(groups)
    mean = sum(values) / n
    if len(keys) < 2:
        return {"n": n, "clusters": len(keys), "mean": mean, "lo": None, "hi": None}
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        tot = cnt = 0.0
        for _k in range(len(keys)):
            g = groups[keys[rng.randrange(len(keys))]]
            tot += sum(g)
            cnt += len(g)
        means.append(tot / cnt)
    means.sort()
    lo = means[int(math.floor(alpha / 2 * n_boot))]
    hi = means[min(n_boot - 1, int(math.ceil((1 - alpha / 2) * n_boot)) - 1)]
    return {"n": n, "clusters": len(keys), "mean": mean, "lo": lo, "hi": hi}


def ols(xs: list[float], ys: list[float]) -> Optional[dict]:
    """y = a + b x with a plain SE on b (clustering is left to the caller's
    judgement; this is a diagnostic, not the pass criterion)."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    resid = [y - a - b * x for x, y in zip(xs, ys)]
    s2 = sum(r * r for r in resid) / (n - 2)
    se = math.sqrt(s2 / sxx) if s2 > 0 else 0.0
    return {"n": n, "beta": b, "se": se, "t": (b / se if se > 0 else float("inf"))}


def summarise(decisions: list[Graded], bets: list[Graded],
              funnel: dict, min_graded: int = MIN_GRADED_FOR_VERDICT) -> dict:
    """The numbers the pass criterion is read from."""
    def mean_ci(rows: list[Graded]):
        return cluster_bootstrap_mean([g.clv_net for g in rows],
                                      [g.cluster for g in rows])

    by_sport: dict[str, dict] = {}
    for g in decisions:
        d = by_sport.setdefault(g.sport, {"bet": [], "skip": []})
        d[g.kind].append(g)
    sports = {}
    for sport, d in sorted(by_sport.items()):
        sports[sport] = {
            "bets": mean_ci(d["bet"]),
            "skips": mean_ci(d["skip"]),
            "all": mean_ci(d["bet"] + d["skip"]),
        }

    taken = [g for g in decisions if g.kind == "bet"]
    overall = mean_ci(taken)
    all_dec = mean_ci(decisions)

    # Venue vs sharp at decision time: how often was the venue mispriced
    # against Pinnacle by more than the fee? That is the opportunity set a
    # sharp-anchored strategy would have had, independent of the model.
    gaps = []
    beyond_fee = 0
    for g in decisions:
        if g.sharp_then_yes is None:
            continue
        gap = g.sharp_then_yes - g.venue_mid_yes
        gaps.append(abs(gap))
        side_price = g.venue_mid_yes if gap > 0 else 1.0 - g.venue_mid_yes
        if abs(gap) > g.fee and 0.0 < side_price < 1.0:
            beyond_fee += 1
    gap_stats = None
    if gaps:
        gaps_sorted = sorted(gaps)
        gap_stats = {"n": len(gaps), "mean_abs": sum(gaps) / len(gaps),
                     "median_abs": gaps_sorted[len(gaps) // 2],
                     "beyond_fee": beyond_fee,
                     "beyond_fee_share": beyond_fee / len(gaps)}

    # Does model disagreement with the venue predict where Pinnacle closes?
    xs, ys = [], []
    for g in decisions:
        if g.model_yes is None:
            continue
        xs.append(g.model_yes - g.venue_mid_yes)
        ys.append(g.sharp_close_yes - g.venue_mid_yes)
    beta = ols(xs, ys)

    # Expected-vs-realised: among settled bets, does net CLV line up with
    # realised return per dollar? (Data Golf's 1:1 check.)
    settled = [g for g in bets if g.kind == "settled" and g.stake]
    exp_vs_real = None
    if settled:
        exp = sum(g.clv_net / g.price for g in settled) / len(settled)
        real = sum((g.pnl or 0.0) / g.stake for g in settled) / len(settled)
        exp_vs_real = {"n": len(settled), "expected_roi": exp,
                       "realised_roi": real}

    n_graded = len(decisions)
    if n_graded < min_graded:
        verdict = (f"INSUFFICIENT — {n_graded} graded decisions, criterion "
                   f"needs {min_graded}")
    elif overall is None or overall["lo"] is None:
        verdict = "INSUFFICIENT — no taken bets graded"
    elif overall["mean"] > 0 and overall["lo"] > 0:
        verdict = (f"PASS — mean net CLV {overall['mean']:+.4f} "
                   f"[{overall['lo']:+.4f}, {overall['hi']:+.4f}] on "
                   f"{overall['n']} bets")
    else:
        verdict = (f"FAIL — mean net CLV {overall['mean']:+.4f} "
                   f"[{overall['lo']:+.4f}, {overall['hi']:+.4f}] on "
                   f"{overall['n']} bets does not clear zero")
    return {"funnel": funnel, "n_graded": n_graded, "taken": overall,
            "all_decisions": all_dec, "by_sport": sports, "gap": gap_stats,
            "beta": beta, "expected_vs_realised": exp_vs_real,
            "bets_graded": len(bets), "verdict": verdict}


def _fmt_ci(d: Optional[dict]) -> str:
    if not d:
        return "—"
    if d["lo"] is None:
        return f"{d['mean']:+.4f} (n={d['n']}, CI needs 2+ events)"
    return (f"{d['mean']:+.4f} [{d['lo']:+.4f}, {d['hi']:+.4f}] "
            f"n={d['n']} events={d['clusters']}")


def format_report(rep: dict, cfg: SharpConfig) -> str:
    f = {k: rep["funnel"].get(k, 0) for k in
         ("decisions", "no_meta", "no_event", "no_close", "graded")}
    lines = [
        f"sharp-line CLV vs {cfg.sharp_book} close (lead >= "
        f"{cfg.min_lead_minutes:.0f} min), net of venue taker fee",
        f"funnel: decisions={f['decisions']} no_meta={f['no_meta']} "
        f"no_event={f['no_event']} no_close={f['no_close']} graded={f['graded']}",
        f"taken bets:     {_fmt_ci(rep['taken'])}",
        f"all decisions:  {_fmt_ci(rep['all_decisions'])}  (skips scored at mid, "
        f"model-preferred side)",
    ]
    for sport, d in rep["by_sport"].items():
        lines.append(f"  {sport:<13} bets {_fmt_ci(d['bets'])}")
        lines.append(f"  {'':<13} skips {_fmt_ci(d['skips'])}")
    g = rep["gap"]
    if g:
        lines.append(f"venue vs sharp at decision time: mean|gap|={g['mean_abs']:.4f} "
                     f"median={g['median_abs']:.4f}; beyond fee in "
                     f"{g['beyond_fee']}/{g['n']} ({g['beyond_fee_share']:.1%})")
    b = rep["beta"]
    if b:
        lines.append(f"beta of (sharp close - venue mid) on (model - venue mid): "
                     f"{b['beta']:+.4f} se={b['se']:.4f} t={b['t']:+.2f} n={b['n']}")
    e = rep["expected_vs_realised"]
    if e:
        lines.append(f"settled bets: expected ROI (CLV/price) {e['expected_roi']:+.4f} "
                     f"vs realised {e['realised_roi']:+.4f} on {e['n']} "
                     f"(1:1 is the healthy pattern)")
    lines.append(f"bets with a sharp close written: {rep['bets_graded']}")
    lines.append(f"verdict: {rep['verdict']}")
    return "\n".join(lines)


def run_report(store: Store, cfg: SharpConfig, account: str = "sim",
               mode: str = "paper", since_ts: str | None = None,
               fee_for: FeeFor = default_fee_for, write: bool = True) -> dict:
    grader = Grader(store, cfg, fee_for)
    decisions, funnel = grader.grade_decisions(account, since_ts)
    bets = grader.grade_bets(mode, write=write)
    return summarise(decisions, bets, funnel)
