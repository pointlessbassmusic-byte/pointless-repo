"""Reddit team-news recorder (DATA ONLY) + the pre-registered news-veto test.

Source policy: Reddit's official OAuth API only, as an application-only
"script" app (client-credentials grant: read-only, no user account is
acted for), with the descriptive User-Agent Reddit's API rules ask for and
a request rate far under the free tier's 100/minute. The public `.json`
endpoints are NOT used: they refuse datacenter clients, and we respect
that instead of working around it (see signals/chatter.py). Keys come from
.env: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, optionally REDDIT_USERNAME
(only for the User-Agent). Without keys the recorder refuses to start.

What is stored, per post on a watched subreddit's /new feed: its id,
subreddit, the poster's created time, the time THIS process first saw it,
the title, and which news terms (injury / lineup / scratch vocabulary) the
title carries. No bodies, no authors, no comments.

The question this exists to answer (docs/NEWS_VETO_PREREG_2026-10-13.md):
do the touch-maker's fills that land shortly after team news reached
Reddit lose more against the close than fills that do not? A resting $1
order is filled by whoever wants to trade at that price; if that is
mostly people reacting to news the bot has not seen, the fix is a veto
(skip the market for a while), which only ever removes trades. Nothing
here is wired into trading; a PASS moves the veto to a paper shadow first.
"""

from __future__ import annotations

import logging
import os
import random
import re
import sqlite3
import time
from typing import Iterable, Optional

import httpx

from sportsbot.signals.chatter import KEYWORDS

log = logging.getLogger(__name__)

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"
DEFAULT_SUBREDDITS = ("baseball", "hockey", "nfl",
                      "fantasybaseball", "fantasyhockey", "fantasyfootball")
PAUSE = 2.0                 # seconds between requests: ~3/min per sub list, far under the cap

# Team-sport news vocabulary on top of the shared chatter keywords. Kept
# specific; generic words ("news", "update") inflate noise.
NEWS_TERMS = tuple(dict.fromkeys(KEYWORDS + (
    "lineup", "scratch", "placed on", "injured list", "day-to-day", "concussion",
    "will not start", "won't start", "not starting", "late scratch", "inactive",
    "starting goalie", "in goal", "scratches", "illness", "il stint", "activated",
)))

# Nicknames of two words; every other nickname is the last word of the name.
_TWO_WORD = {"red sox", "white sox", "blue jays", "maple leafs", "golden knights",
             "red wings", "blue jackets", "trail blazers"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS reddit_posts (
    id TEXT PRIMARY KEY,
    subreddit TEXT NOT NULL,
    created_utc REAL NOT NULL,
    first_seen REAL NOT NULL,
    title TEXT NOT NULL,
    flagged INTEGER NOT NULL,
    terms TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reddit_seen ON reddit_posts (first_seen);
"""


def news_terms(title: str) -> list[str]:
    """The news terms a title carries (pure; unit-tested)."""
    low = " " + (title or "").lower() + " "
    return [t for t in NEWS_TERMS if t in low]


def team_aliases(label: Optional[str], sport=None) -> set[str]:
    """Lower-case strings that name a team in a post title: the label, its
    canonical full name where the sport has a table, and the nickname.
    Strings under 4 characters are dropped (too many false hits)."""
    if not label:
        return set()
    names = {str(label).strip().lower()}
    if sport is not None:
        try:
            from sportsbot.data.teams import canonical

            full = canonical(sport, label)
            if full:
                names.add(full.lower())
        except Exception:
            pass
    out = set()
    for n in names:
        out.add(n)
        words = n.split()
        if len(words) >= 2:
            two = " ".join(words[-2:])
            out.add(two if two in _TWO_WORD else words[-1])
    return {a for a in out if len(a) >= 4}


def mentions(title: str, aliases: Iterable[str]) -> bool:
    low = (title or "").lower()
    return any(re.search(r"(?<![a-z])" + re.escape(a) + r"(?![a-z])", low) for a in aliases)


class RedditClient:
    """Application-only OAuth client. Never retries a 4xx; backs off on 429."""

    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None,
                 username: Optional[str] = None, http: Optional[httpx.Client] = None) -> None:
        self.client_id = client_id or os.environ.get("REDDIT_CLIENT_ID", "").strip()
        self.client_secret = client_secret or os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
        if not (self.client_id and self.client_secret):
            raise RuntimeError("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set (.env) — "
                               "create a 'script' app at reddit.com/prefs/apps")
        user = (username or os.environ.get("REDDIT_USERNAME", "")).strip()
        self.user_agent = ("linux:sportsbot-news-recorder:0.1"
                           + (f" (by /u/{user})" if user else " (research, data only)"))
        self.http = http or httpx.Client(timeout=30)
        self._token: Optional[str] = None
        self._token_exp = 0.0

    def _auth(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        resp = self.http.post(TOKEN_URL, data={"grant_type": "client_credentials"},
                              auth=(self.client_id, self.client_secret),
                              headers={"User-Agent": self.user_agent})
        resp.raise_for_status()
        data = resp.json()
        if "access_token" not in data:
            raise RuntimeError(f"reddit token refused: {data.get('error', 'unknown')}")
        self._token = data["access_token"]
        self._token_exp = time.time() + float(data.get("expires_in", 3600))
        return self._token

    def new_posts(self, subreddit: str, limit: int = 100) -> list[dict]:
        resp = self.http.get(f"{API_BASE}/r/{subreddit}/new", params={"limit": limit, "raw_json": 1},
                             headers={"User-Agent": self.user_agent,
                                      "Authorization": f"bearer {self._auth()}"})
        if resp.status_code == 429:
            wait = float(resp.headers.get("x-ratelimit-reset", "60") or 60)
            log.warning("reddit rate limited; sleeping %.0fs", wait)
            time.sleep(min(wait, 600))
            return []
        resp.raise_for_status()
        return [c.get("data", {}) for c in resp.json().get("data", {}).get("children", [])]


class NewsRecorder:
    def __init__(self, db_path: str, client, subreddits=DEFAULT_SUBREDDITS,
                 interval: float = 120.0) -> None:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)
        self.client = client
        self.subreddits = tuple(subreddits)
        self.interval = interval

    def poll_once(self, now: Optional[float] = None, pause: float = PAUSE) -> int:
        """Store every post not seen before; `first_seen` is THIS process's
        clock, which is what a decision at time t could have known."""
        now = time.time() if now is None else now
        new = 0
        for sub in self.subreddits:
            try:
                posts = self.client.new_posts(sub)
            except Exception as exc:
                log.warning("reddit r/%s failed: %s", sub, exc)
                continue
            for p in posts:
                pid, title = p.get("id"), str(p.get("title") or "")
                if not pid:
                    continue
                terms = news_terms(title)
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO reddit_posts (id, subreddit, created_utc, first_seen,"
                    " title, flagged, terms) VALUES (?,?,?,?,?,?,?)",
                    (pid, sub, float(p.get("created_utc") or now), now, title[:300],
                     int(bool(terms)), ",".join(terms)))
                new += cur.rowcount
            if pause:
                time.sleep(pause)
        self.conn.commit()
        return new

    def run_forever(self) -> None:
        while True:
            t0 = time.time()
            try:
                log.info("reddit: %d new posts", self.poll_once())
            except Exception:
                log.exception("reddit poll failed")
            # jitter so the request pattern is not a metronome
            time.sleep(max(10.0, self.interval - (time.time() - t0)) + random.uniform(0, 5))


# ------------------------------------------------------------------ the test
WINDOW_S = 3600.0          # pre-registered: news in the hour before the fill
MIN_PER_GROUP = 20         # pre-registered: both groups need this many graded fills


def _flagged_before(conn, aliases: set[str], t: float, window: float) -> bool:
    if not aliases:
        return False
    rows = conn.execute(
        "SELECT title FROM reddit_posts WHERE flagged=1 AND first_seen<=? AND created_utc>=?",
        (t, t - window)).fetchall()
    return any(mentions(r[0], aliases) for r in rows)


def _diff_bootstrap(a: list[tuple[str, float]], b: list[tuple[str, float]],
                    n_boot: int = 2000, seed: int = 13) -> tuple[float, float, float]:
    """mean(a) - mean(b) with a market-clustered bootstrap 95% interval
    (markets resampled within each group)."""
    rng = random.Random(seed)

    def clusters(rows):
        out: dict[str, list[float]] = {}
        for k, v in rows:
            out.setdefault(k, []).append(v)
        return list(out.values())

    ca, cb = clusters(a), clusters(b)

    def mean_of(cs):
        vals = [v for c in cs for v in c]
        return sum(vals) / len(vals)

    est = mean_of(ca) - mean_of(cb)
    diffs = sorted(mean_of([rng.choice(ca) for _ in ca]) - mean_of([rng.choice(cb) for _ in cb])
                   for _ in range(n_boot))
    return est, diffs[int(0.025 * n_boot)], diffs[int(0.975 * n_boot) - 1]


def news_veto_test(store, reddit_db: str, mode: Optional[str] = "live",
                   window: float = WINDOW_S, min_per_group: int = MIN_PER_GROUP) -> dict:
    """The pre-registered read-out. Population: touch-maker fills of `mode`
    graded against the venue close. Exposure: a news-flagged post naming
    either team, created in the `window` before the fill AND first seen by
    the recorder no later than the fill. PASS iff both groups reach
    `min_per_group` and the 95% lower bound of
    mean CLV(quiet) - mean CLV(flagged) is above zero."""
    from sportsbot.bot.maker_report import fill_rows

    conn = sqlite3.connect(reddit_db)
    span = conn.execute("SELECT MIN(first_seen), MAX(first_seen) FROM reddit_posts").fetchone()
    flagged, quiet = [], []
    excluded = {"no_close": 0, "no_teams": 0, "outside_recording": 0}
    for r in fill_rows(store, mode=mode):
        if r["venue_clv"] is None:
            excluded["no_close"] += 1
            continue
        t = r["filled_ts"]
        if span[0] is None or t is None or t < span[0] + window or t > span[1]:
            excluded["outside_recording"] += 1   # recorder was not watching the whole window
            continue
        meta = store.market_meta(r["market"]) or {}
        sport = None
        try:
            from sportsbot.core.types import Sport

            sport = Sport(meta["sport"]) if meta.get("sport") else None
        except ValueError:
            sport = None
        aliases = team_aliases(meta.get("home"), sport) | team_aliases(meta.get("away"), sport)
        if not aliases:
            excluded["no_teams"] += 1
            continue
        (flagged if _flagged_before(conn, aliases, t, window) else quiet).append(
            (r["market"], r["venue_clv"]))
    out = {"window_s": window, "n_flagged": len(flagged), "n_quiet": len(quiet),
           "excluded": excluded, "verdict": "PENDING"}
    for name, rows in (("flagged", flagged), ("quiet", quiet)):
        out[f"mean_clv_{name}"] = round(sum(v for _, v in rows) / len(rows), 4) if rows else None
    if len(flagged) >= min_per_group and len(quiet) >= min_per_group:
        est, lo, hi = _diff_bootstrap(quiet, flagged)
        out["diff_quiet_minus_flagged"] = {"est": round(est, 4), "lo": round(lo, 4),
                                           "hi": round(hi, 4)}
        out["verdict"] = "PASS" if lo > 0 else "FAIL"
    return out


def format_news_veto(rep: dict) -> str:
    lines = [f"news-veto test (window {rep['window_s'] / 60:.0f} min): "
             f"{rep['n_flagged']} flagged fills, {rep['n_quiet']} quiet fills "
             f"(excluded {rep['excluded']})",
             f"mean venue CLV: flagged {rep['mean_clv_flagged']}, quiet {rep['mean_clv_quiet']}"]
    d = rep.get("diff_quiet_minus_flagged")
    if d:
        lines.append(f"quiet - flagged = {d['est']:+.4f} [95% {d['lo']:+.4f}, {d['hi']:+.4f}]")
    else:
        lines.append(f"need >= {MIN_PER_GROUP} graded fills in each group before a verdict")
    lines.append(f"verdict: {rep['verdict']}  (pre-registered: docs/NEWS_VETO_PREREG_2026-10-13.md)")
    return "\n".join(lines)
