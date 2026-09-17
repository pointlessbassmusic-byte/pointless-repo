"""Public social-chatter signal collector (Bluesky) + correlation report.

Source policy: only openly published APIs, accessed as intended —
  * Bluesky's public AppView search (no auth, supports since/until), used
    with a descriptive User-Agent and low request volume.
  * Reddit's JSON endpoints are NOT used: they refuse datacenter clients,
    and we respect that instead of working around it.

What is collected: per entity (team/player string), the number of recent
public posts matching the entity and how many of those contain event-relevant
keywords (injury/scratch/withdrawal/weather-delay vocabulary). Rows are
timestamped at collection (decision-time discipline: the report only ever
joins chatter collected BEFORE an event's decision time).

This is a research feed. Nothing here is wired into trading; the report
measures whether chatter correlates with market residuals (outcome minus
market probability) so a future strategy change can be justified — or
rejected — with evidence.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from typing import Iterable, Optional

import httpx

log = logging.getLogger(__name__)

SEARCH_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
FALLBACK_URL = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
USER_AGENT = "sportsbot-substrate (research; repo: pointlessbassmusic-byte/pointless-repo)"
PAUSE = 2.0                     # seconds between requests — stay polite
MAX_PAGES = 3                   # per entity per scan (<= 300 posts)

# Event-relevant vocabulary. Deliberately specific; generic words inflate noise.
KEYWORDS = (
    "injury", "injured", "scratched", "questionable", "doubtful", "ruled out",
    "walkover", "retired hurt", "withdrew", "withdrawal", "out of the lineup",
    "rain delay", "postponed", "suspended", "weather delay", "cramping",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS chatter (
    id INTEGER PRIMARY KEY,
    collected_ts REAL NOT NULL,
    entity TEXT NOT NULL,
    window_hours REAL NOT NULL,
    posts INTEGER NOT NULL,
    flagged INTEGER NOT NULL,
    flagged_terms TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chatter_entity_ts ON chatter (entity, collected_ts);
"""


def count_flags(texts: Iterable[str]) -> tuple[int, dict[str, int]]:
    """(#texts containing any keyword, per-keyword counts). Pure; unit-tested."""
    per: dict[str, int] = {}
    flagged = 0
    for text in texts:
        low = (text or "").lower()
        hit = False
        for kw in KEYWORDS:
            if kw in low:
                per[kw] = per.get(kw, 0) + 1
                hit = True
        flagged += hit
    return flagged, per


class BlueskyChatter:
    def __init__(self, db_path: str = "data/chatter.sqlite"):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # -- collection ---------------------------------------------------------
    def _search(self, query: str, since_iso: Optional[str]) -> list[str]:
        """Collect up to MAX_PAGES of matching post texts. Cursors are bound to
        the host that issued them, so the first host that answers is kept for
        the whole query; a failed later page returns what was already
        collected (a partial count still counts)."""
        texts: list[str] = []
        cursor = None
        host: Optional[str] = None
        for page in range(MAX_PAGES):
            params: dict = {"q": query, "limit": 100}
            if since_iso:
                params["since"] = since_iso
            if cursor:
                params["cursor"] = cursor
            data = None
            last_exc: Optional[Exception] = None
            for url in ((host,) if host else (SEARCH_URL, FALLBACK_URL)):
                try:
                    resp = httpx.get(url, params=params,
                                     headers={"User-Agent": USER_AGENT}, timeout=30)
                    resp.raise_for_status()
                    data = resp.json()
                    host = url
                    break
                except Exception as exc:  # noqa: BLE001 — try the fallback host
                    last_exc = exc
            if data is None:
                if page == 0:
                    # anonymous endpoint rate limit — one polite retry
                    time.sleep(5.0)
                    try:
                        resp = httpx.get(FALLBACK_URL, params=params,
                                         headers={"User-Agent": USER_AGENT}, timeout=30)
                        resp.raise_for_status()
                        data = resp.json()
                        host = FALLBACK_URL
                    except Exception as exc:  # noqa: BLE001
                        raise RuntimeError(
                            f"bluesky search failed for {query!r}: {exc}") from exc
                if data is None:
                    log.info("bluesky pagination stopped for %r after %d posts (%s)",
                             query, len(texts), last_exc)
                    break
            for post in data.get("posts", []):
                texts.append(str(post.get("record", {}).get("text", "")))
            cursor = data.get("cursor")
            if not cursor or not data.get("posts"):
                break
            time.sleep(PAUSE)
        return texts

    def scan(self, entities: list[str], window_hours: float = 24.0) -> list[dict]:
        """Collect and persist one chatter row per entity. Returns the rows."""
        since_iso = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - window_hours * 3600))
        out = []
        for entity in entities:
            entity = entity.strip()
            if not entity:
                continue
            try:
                texts = self._search(entity, since_iso)
            except RuntimeError as exc:
                log.warning("%s", exc)
                continue
            flagged, per = count_flags(texts)
            row = {"collected_ts": time.time(), "entity": entity,
                   "window_hours": window_hours, "posts": len(texts),
                   "flagged": flagged, "flagged_terms": per}
            self.conn.execute(
                "INSERT INTO chatter (collected_ts, entity, window_hours, posts,"
                " flagged, flagged_terms) VALUES (?,?,?,?,?,?)",
                (row["collected_ts"], entity, window_hours, len(texts), flagged,
                 json.dumps(per, sort_keys=True)))
            out.append(row)
            time.sleep(PAUSE)
        self.conn.commit()
        return out


def entities_from_events_csv(path: str, max_entities: int = 40) -> list[str]:
    """Derive searchable entity strings from ingest-schema event_ids (slugs).
    'mlb-lad-nyy-...' style tokens are too short to search; keep words >= 4
    chars from the id, deduped, longest first."""
    import csv
    seen: dict[str, None] = {}
    with open(path) as fh:
        for r in csv.DictReader(fh):
            for tok in re.split(r"[^a-zA-Z]+", r.get("event_id", "")):
                if len(tok) >= 4 and not tok.isdigit():
                    seen.setdefault(tok.lower(), None)
    return sorted(seen, key=len, reverse=True)[:max_entities]


def correlation_report(db_path: str, events_csv: str) -> str:
    """Spearman correlation between pre-decision flagged-chatter share and the
    market residual |outcome - market_prob| for events whose id contains the
    entity. Chatter must be collected BEFORE the event's decision time."""
    import csv

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT collected_ts, entity, posts, flagged"
                        " FROM chatter WHERE posts > 0").fetchall()
    events = []
    with open(events_csv) as fh:
        for r in csv.DictReader(fh):
            if r.get("outcome") not in ("", None):
                events.append((r["event_id"].lower(), float(r["close_time"]),
                               float(r["market_prob"]), int(r["outcome"])))

    pairs = []  # (flag_share, |residual|)
    for eid, close_t, mprob, outcome in events:
        best = None
        for ts, entity, posts, flagged in rows:
            if ts <= close_t and entity.lower() in eid:
                if best is None or ts > best[0]:
                    best = (ts, flagged / posts)
        if best is not None:
            pairs.append((best[1], abs(outcome - mprob)))

    if len(pairs) < 30:
        return (f"insufficient joined data: {len(pairs)} chatter-event pairs "
                f"(need >= 30). Keep the scan running; pairs accrue as events "
                f"resolve after collection.")

    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        rk = [0.0] * len(xs)
        for pos, i in enumerate(order):
            rk[i] = pos
        return rk

    ra, rb = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    n = len(pairs)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((a - ma) * (b - mb) for a, b in zip(ra, rb))
    va = sum((a - ma) ** 2 for a in ra) ** 0.5
    vb = sum((b - mb) ** 2 for b in rb) ** 0.5
    rho = cov / (va * vb) if va and vb else 0.0
    return (f"n={n} chatter-event pairs; Spearman(flag_share, |outcome - market|) "
            f"= {rho:+.3f}. |rho| < ~0.2 on this n is noise — demand consistency "
            f"across weeks before believing it.")
