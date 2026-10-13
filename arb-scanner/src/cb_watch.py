"""G10 central-bank decision recorder: the Fed rule, run for the six other
banks a deep OIS market prices (ECB, BoE, BoJ, BoC, RBA, RBNZ). Records,
never trades — same as fed_watch, whose evaluator it reuses unchanged.

Why (EDGE_VERDICT Result 20, `python -m src.cb_study` in polymarket-edge):
across 15 banks on Polymarket the rule (decision bucket at >= 0.90 within 7
days) lost 3 of 87 fires and returned -0.7%, but the G10 banks fired 57 times
with no loss. That split was drawn after the losses were seen, so it needs an
out-of-sample record, and this recorder builds it about seven times faster
than the Fed alone (~50 G10 fires a year against ~8).

Gates, per (bank, meeting, bucket):
* two-venue (`gate = two_venue`): exactly the Fed gates. A venue's ask >=
  min_price, 0 < lead <= max_lead_days to the earliest close across venues
  (Kalshi closes at the announcement), and the other venue's mid >=
  agree_min. Kalshi lists all six (KXCBDECISION<EU|ENGLAND|JAPAN|CANADA|
  AUSTRALIA|NZ>), though not always every meeting Polymarket lists.
* single-venue (`gate = single_venue`): only when Kalshi lists no market for
  the bucket at all (e.g. a meeting Kalshi has not opened yet). A bank whose
  Kalshi read fails this cycle is skipped, never single-venue. Polymarket's own ask, the same
  price and window, and a lead of at least single_venue_min_lead_days,
  because Polymarket's endDate can fall up to ~1 day after the announcement
  (ECB Oct 2026: 03:59Z the next day; RBA Nov 2026: 04:59Z the next day).
  This is the rule Result 20 measured. It is scored separately and is never
  pooled with the two-venue record.

Rows settle from Kalshi results (two-venue) or Polymarket's own resolution
(single-venue). `python -m src.cb_watch --report` prints both records.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .fed_watch import FedSignal, FedWatcher, state_lines
from .feeds import BinaryMarket

log = logging.getLogger(__name__)

# bank -> (Kalshi series, name pattern in Polymarket questions)
BANKS: dict[str, tuple[str, str]] = {
    "ecb": ("KXCBDECISIONEU", r"\bECB\b"),
    "boe": ("KXCBDECISIONENGLAND", r"\bBank of England\b"),
    "boj": ("KXCBDECISIONJAPAN", r"\bBank of Japan\b"),
    "boc": ("KXCBDECISIONCANADA", r"\bBank of Canada\b"),
    "rba": ("KXCBDECISIONAUSTRALIA", r"\bReserve Bank of Australia\b"),
    "rbnz": ("KXCBDECISIONNZ", r"\bReserve Bank of New Zealand\b"),
}
POLY_TAGS = ("Global-Rates", "ecb")      # Gamma tags carrying these events

SCHEMA = """
CREATE TABLE IF NOT EXISTS cb_signals (
    id INTEGER PRIMARY KEY,
    detected_at TEXT NOT NULL,
    bank TEXT NOT NULL,             -- ecb | boe | boj | boc | rba | rbnz
    meeting TEXT NOT NULL,          -- YYYY-MM
    bucket TEXT NOT NULL,           -- cut_50plus | cut_25 | hold | hike_25 | hike_50plus
    platform TEXT NOT NULL,
    market_id TEXT NOT NULL,        -- ticker (kalshi) or YES token (polymarket)
    kalshi_ticker TEXT,             -- settlement source when the bank is on Kalshi
    gate TEXT NOT NULL,             -- two_venue | single_venue
    ask REAL NOT NULL,
    other_mid REAL,
    lead_days REAL NOT NULL,
    net_return REAL NOT NULL,
    stake_usd REAL NOT NULL,
    result TEXT,
    settled_at TEXT,
    UNIQUE (bank, meeting, bucket, platform)
);
"""

_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august",
                "september", "october", "november", "december"]
_SERIES_TO_BANK = {series: bank for bank, (series, _) in BANKS.items()}

# KXCBDECISIONENGLAND-26NOV05-H25P, KXCBDECISIONCANADA-26OCT-H0
_KALSHI_RE = re.compile(r"^(KXCBDECISION[A-Z]+)-(\d{2})([A-Z]{3})\d{0,2}-([A-Z0-9]+)$")
_KALSHI_BUCKETS = {"HOLD": "hold", "H0": "hold", "H25": "hike_25", "H25P": "hike_50plus",
                   "H26": "hike_50plus", "C25": "cut_25", "C25P": "cut_50plus",
                   "C26": "cut_50plus"}
_MONTH_RE = re.compile(r"(?i)\b(" + "|".join(_MONTH_NAMES) + r")\b(?:\s+(\d{4}))?")


def parse_kalshi(ticker: str) -> tuple[str, str] | None:
    """('<bank> YYYY-MM', bucket) from a KXCBDECISION<bank> ticker."""
    m = _KALSHI_RE.match(ticker or "")
    if not m:
        return None
    series, yy, mon, suffix = m.groups()
    bank, bucket = _SERIES_TO_BANK.get(series), _KALSHI_BUCKETS.get(suffix)
    if bank is None or bucket is None or mon not in _MONTHS:
        return None
    return f"{bank} 20{yy}-{_MONTHS.index(mon) + 1:02d}", bucket


def parse_polymarket(question: str, close: datetime | None = None) -> tuple[str, str] | None:
    """('<bank> YYYY-MM', bucket) from a Polymarket decision question. BoC's
    wording carries no year ("at the October interest rate announcement"), so
    the market's own close supplies it."""
    q = question or ""
    bank = next((b for b, (_, pat) in BANKS.items() if re.search(pat, q)), None)
    if bank is None:
        return None
    mm = _MONTH_RE.search(q)
    if not mm:
        return None
    month = _MONTH_NAMES.index(mm.group(1).lower()) + 1
    year = int(mm.group(2)) if mm.group(2) else (close.year if close else None)
    if year is None:
        return None
    low = q.lower()
    if "no change" in low:
        bucket = "hold"
    else:
        size = re.search(r"(\d+)\s*(\+?)\s*bps", low)
        up = re.search(r"\b(increase|increases|hike|raise)\b", low)
        down = re.search(r"\b(decrease|decreases|cut|lower)\b", low)
        if not size or bool(up) == bool(down):
            return None
        big = size.group(2) == "+" or int(size.group(1)) >= 50
        bucket = f"{'hike' if up else 'cut'}_{'50plus' if big else size.group(1)}"
        if bucket not in ("hike_25", "hike_50plus", "cut_25", "cut_50plus"):
            return None
    return f"{bank} {year}-{month:02d}", bucket


def parse(market: BinaryMarket) -> tuple[str, str] | None:
    if market.platform == "kalshi":
        return parse_kalshi(market.market_id)
    return parse_polymarket(market.question, market.close_time)


def split_meeting(meeting: str) -> tuple[str, str]:
    bank, ym = meeting.split(" ", 1)
    return bank, ym


def evaluate(watcher: FedWatcher, markets: list[BinaryMarket],
             now: datetime | None = None) -> list[FedSignal]:
    return watcher.evaluate(markets, now=now, parse_fn=parse, allow_single_venue=True)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def record(conn: sqlite3.Connection, signals: list[FedSignal]) -> int:
    """First fires only, as in fed_watch."""
    ensure_schema(conn)
    n = 0
    for s in signals:
        bank, ym = split_meeting(s.meeting)
        gate = "two_venue" if s.other_mid is not None else "single_venue"
        cur = conn.execute(
            "INSERT OR IGNORE INTO cb_signals (detected_at, bank, meeting, bucket, platform,"
            " market_id, kalshi_ticker, gate, ask, other_mid, lead_days, net_return, stake_usd)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s.detected_at, bank, ym, s.bucket, s.platform, s.market_id, s.kalshi_ticker, gate,
             s.ask, s.other_mid, s.lead_days, s.net_return, s.stake_usd))
        if cur.rowcount:
            n += 1
            log.info("CB rule fired (%s): %s %s %s YES@%.3f on %s, %.1fd out, +%.2f%% if it "
                     "pays", gate, bank, ym, s.bucket, s.ask, s.platform, s.lead_days,
                     s.net_return * 100)
    conn.commit()
    return n


def settle(conn: sqlite3.Connection, results: dict[str, str],
           now: datetime | None = None) -> int:
    """Score open rows: by the Kalshi counterpart's ticker when there is one,
    else by the row's own market id (a Polymarket YES token)."""
    ensure_schema(conn)
    ts = (now or datetime.now(timezone.utc)).isoformat()
    n = 0
    for row_id, ticker, market_id in conn.execute(
            "SELECT id, kalshi_ticker, market_id FROM cb_signals WHERE result IS NULL").fetchall():
        res = results.get(ticker or "") if ticker else results.get(market_id)
        if res in ("yes", "no"):
            conn.execute("UPDATE cb_signals SET result=?, settled_at=? WHERE id=?",
                         (res, ts, row_id))
            n += 1
    conn.commit()
    return n


def open_poly_tokens(conn: sqlite3.Connection) -> list[str]:
    ensure_schema(conn)
    return [r[0] for r in conn.execute(
        "SELECT market_id FROM cb_signals WHERE result IS NULL AND kalshi_ticker IS NULL"
        " AND platform = 'polymarket'")]


def poly_results(http, tokens: list[str]) -> dict[str, str]:
    """YES token -> 'yes'|'no' for resolved Polymarket markets (Gamma)."""
    out: dict[str, str] = {}
    for tok in tokens:
        r = http.get("https://gamma-api.polymarket.com/markets",
                     params={"clob_token_ids": tok, "closed": "true"}, timeout=30)
        if r.status_code != 200:
            continue
        for m in r.json() or []:
            try:
                toks = json.loads(m.get("clobTokenIds") or "[]")
                prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
            except (ValueError, TypeError):
                continue
            if toks and str(toks[0]) == tok and sorted(prices) == [0.0, 1.0]:
                out[tok] = "yes" if prices[0] == 1.0 else "no"
    return out


def scorecard(conn: sqlite3.Connection, gate: str) -> dict:
    """Per-gate record, same arithmetic as fed_watch.scorecard."""
    ensure_schema(conn)
    n_fired = conn.execute("SELECT COUNT(*) FROM cb_signals WHERE gate=?", (gate,)).fetchone()[0]
    rows = conn.execute("SELECT net_return, result FROM cb_signals WHERE gate=? AND result IS "
                        "NOT NULL", (gate,)).fetchall()
    n = len(rows)
    paid = [r for r, res in rows if res == "yes"]
    mean = sum(r if res == "yes" else -1.0 for r, res in rows) / n if n else 0.0
    win = sum(paid) / len(paid) if paid else 0.0
    upper = None if n == 0 else (3 / n if len(paid) == n else (n - len(paid)) / n)
    return {"gate": gate, "fired": n_fired, "settled": n, "paid": len(paid),
            "mean_net_return": round(mean, 4),
            "breakeven_surprise": round(win / (1 + win), 4) if win > 0 else 0.0,
            "surprise_upper95": None if upper is None else round(upper, 4)}


EXPORT_FIELDS = ["detected_at", "bank", "meeting", "bucket", "platform", "market_id",
                 "kalshi_ticker", "gate", "ask", "other_mid", "lead_days", "net_return",
                 "stake_usd", "result", "settled_at"]


def export_csv(conn: sqlite3.Connection, path: Path) -> int:
    ensure_schema(conn)
    rows = conn.execute(f"SELECT {', '.join(EXPORT_FIELDS)} FROM cb_signals "
                        "ORDER BY bank, meeting, bucket, platform").fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(EXPORT_FIELDS)
        w.writerows(rows)
    return len(rows)


def fetch(kalshi, poly, pause: float = 1.0,
          sleep=None) -> tuple[list[BinaryMarket], dict[str, str]]:
    """Open markets on both venues and every Kalshi settlement for the six
    series. A bank whose Kalshi read FAILS this cycle is dropped entirely:
    otherwise its Polymarket buckets would look Kalshi-less and could fire on
    the single-venue path, skipping the agreement gate on a fetch error.
    A series that answers with no markets is a genuine single-venue bank."""
    import time
    sleep = sleep or time.sleep
    markets: list[BinaryMarket] = []
    results: dict[str, str] = {}
    failed: set[str] = set()
    for bank, (series, _) in BANKS.items():
        try:
            markets += kalshi.series_markets(series)
            results.update(kalshi.series_results(series))
        except Exception as exc:  # noqa: BLE001 — one series must not sink the rest
            log.warning("cb watch: kalshi %s unavailable this cycle (%s); %s skipped",
                        series, exc, bank)
            failed.add(bank)
        sleep(pause)                       # Kalshi rate-limits back-to-back series reads
    seen = set()
    for tag in POLY_TAGS:
        for m in poly.tagged_markets(tag):
            key = parse(m)
            if m.market_id in seen or key is None or split_meeting(key[0])[0] in failed:
                continue
            seen.add(m.market_id)
            markets.append(m)
    return markets, results


def run_cycle(conn: sqlite3.Connection, watcher: FedWatcher, markets: list[BinaryMarket],
              settled: dict[str, str]) -> tuple[int, int]:
    return record(conn, evaluate(watcher, markets)), settle(conn, settled)


def main() -> None:
    import yaml

    from .feeds import KalshiFeed, PolymarketFeed
    from .main import ROOT, open_db

    ap = argparse.ArgumentParser(description="G10 central-bank decision recorder (never trades)")
    ap.add_argument("--report", action="store_true",
                    help="print both records and the live state without recording")
    ap.add_argument("--export", default="docs/cb_signals.csv")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f) or {}
    conn = open_db(ROOT / cfg.get("storage", {}).get("db_path", "data/arb.db"))
    watcher = FedWatcher(cfg.get("cb_watch", {}))
    kalshi, poly = KalshiFeed(), PolymarketFeed()
    markets, settled = fetch(kalshi, poly)
    if not args.report:
        settled.update(poly_results(poly.http, open_poly_tokens(conn)))
        new, done = run_cycle(conn, watcher, markets, settled)
        n = export_csv(conn, ROOT / args.export)
        print(f"recorded {new} new fire(s), settled {done} row(s); {n} row(s) exported to "
              f"{args.export}")
    print("\n".join(state_lines(markets, parse_fn=parse)) or "no open G10 decision markets")
    for gate in ("two_venue", "single_venue"):
        r = scorecard(conn, gate)
        bound = "n/a" if r["surprise_upper95"] is None else f"{r['surprise_upper95']:.1%}"
        print(f"\nrecord ({gate}): fired {r['fired']}, settled {r['settled']}, paid {r['paid']}, "
              f"mean net return {r['mean_net_return']:+.2%}, breakeven surprise rate "
              f"{r['breakeven_surprise']:.2%}, surprise-rate upper bound {bound}")


if __name__ == "__main__":
    main()
