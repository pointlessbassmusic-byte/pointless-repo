"""Last Polymarket print at or before a timestamp, on Data API v2.

v1 (`/trades?market=&end=`) was retired 2026-11-02: every read is 410 Gone.
v2 (`/v2/trades?condition=`) returns prints NEWEST FIRST in cursor pages and
silently IGNORES every end-time parameter tried (`end`, `end_ts`, `before`,
`to`, `until`, `max_ts`, ...: all return the latest prints). A port that kept
`end=` would score post-decision prices as "the price before" — a look-ahead
leak. So the pages are walked back until they pass `ts`, and the answer is
the newest print at or before it; a walk that cannot reach `ts` answers None,
never a later print.

Pages are cached per (http, condition) so several horizons on one market walk
its tape once.
"""
from __future__ import annotations

import threading
from typing import Optional

TRADES_V2 = "https://data-api.polymarket.com/v2/trades"
PAGE = 1000
MAX_PAGES = 40

_tapes: dict[tuple[int, str], dict] = {}
_guard = threading.Lock()


def _walk(http, condition_id: str, ts: int, max_pages: int) -> list[dict]:
    key = (id(http), condition_id)
    with _guard:
        tape = _tapes.setdefault(key, {"rows": [], "cursor": None, "done": False, "pages": 0,
                                       "lock": threading.Lock()})
    with tape["lock"]:          # the studies fetch on thread pools
        return _extend(http, condition_id, ts, max_pages, tape)


def _extend(http, condition_id: str, ts: int, max_pages: int, tape: dict) -> list[dict]:
    while not tape["done"] and tape["pages"] < max_pages and (
            not tape["rows"] or int(tape["rows"][-1].get("timestamp", 0)) > ts):
        params = {"condition": condition_id, "limit": PAGE}
        if tape["cursor"]:
            params["cursor"] = tape["cursor"]
        r = http.get(TRADES_V2, params=params, timeout=60)
        r.raise_for_status()
        body = r.json()
        data = body.get("data") if isinstance(body, dict) else body
        tape["pages"] += 1
        if not isinstance(data, list) or not data:
            tape["done"] = True
            break
        tape["rows"].extend(data)
        tape["cursor"] = (body.get("pagination") or {}).get("next_cursor") if isinstance(body, dict) else None
        if not tape["cursor"]:
            tape["done"] = True
    return list(tape["rows"])


def last_print_before(http, condition_id: str, ts: int,
                      max_pages: int = MAX_PAGES) -> Optional[dict]:
    """Newest print with timestamp <= ts, or None (no such print, or the walk
    hit `max_pages` before reaching ts)."""
    rows = _walk(http, condition_id, ts, max_pages)
    best = None
    for t in rows:
        try:
            when = int(t["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        if when <= ts and (best is None or when > int(best["timestamp"])):
            best = t
    return best


def clear_cache() -> None:
    _tapes.clear()
