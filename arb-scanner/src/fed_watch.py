"""Fed decision watcher: the one reference-price edge the resolved-market
study found, run as a recorder so it builds its own settled record.

The rule (docs/RESOLVED_MARKET_STUDY_2026-09-23.md): within 7 days of an
FOMC meeting, when the decision bucket trades at >= 0.90 on one venue and the
other venue agrees, buy it and hold to resolution. 18 meetings, Mar 2024 to
Sep 2026, zero losses; +2.1% per $1 a day out, +3.4% a week out, net of a 1c
spread. The mechanism is that fed-funds futures lead the book and nobody arbs
the last few cents.

This module does not trade (arb-scanner never does). Each cycle it reads the
KXFEDDECISION series on Kalshi and the "Fed Decision in <Month>" events on
Polymarket, evaluates the rule, and records the FIRST price at which it fired
for each (meeting, bucket, venue) as the entry the rule would have taken. Once
Kalshi settles the meeting the rows are scored. `python -m src.fed_watch
--report` prints the live state and the settled record; the pre-live-gate
skill requires that record before anything here meets money.

Sizing is written into every row as a fixed cap, not Kelly: the trade is short
volatility (one surprise costs the stake and erases ~40 wins), and Kelly on a
2-3c edge at 0.95 says to bet half the bankroll.
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .feeds import BinaryMarket

log = logging.getLogger(__name__)

KALSHI_SERIES = "KXFEDDECISION"
POLY_TAG = "fed-rates"

SCHEMA = """
CREATE TABLE IF NOT EXISTS fed_signals (
    id INTEGER PRIMARY KEY,
    detected_at TEXT NOT NULL,
    meeting TEXT NOT NULL,          -- YYYY-MM of the FOMC meeting
    bucket TEXT NOT NULL,           -- cut_50plus | cut_25 | hold | hike_25 | hike_50plus
    platform TEXT NOT NULL,         -- venue the rule fired on
    market_id TEXT NOT NULL,        -- ticker (kalshi) or YES token (polymarket)
    kalshi_ticker TEXT,             -- settlement source for both venues
    ask REAL NOT NULL,              -- entry price the rule would have paid
    other_mid REAL,                 -- the other venue's mid for the same bucket
    lead_days REAL NOT NULL,
    net_return REAL NOT NULL,       -- per $1 staked if the bucket pays, after fees
    stake_usd REAL NOT NULL,
    result TEXT,                    -- yes | no once settled
    settled_at TEXT,
    UNIQUE (meeting, bucket, platform)
);
"""

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
_MONTH_NAMES = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"])}

# KXFEDDECISION-26OCT-H25 -> meeting 2026-10, bucket suffix H25
_KALSHI_RE = re.compile(rf"^{KALSHI_SERIES}-(\d{{2}})([A-Z]{{3}})-([A-Z]\d+)$")
_KALSHI_BUCKETS = {"C26": "cut_50plus", "C25": "cut_25", "H0": "hold",
                   "H25": "hike_25", "H26": "hike_50plus"}

# "Will the Fed decrease interest rates by 25 bps after the October 2026 meeting?"
# "Will there be no change in Fed interest rates after the October 2026 meeting?"
_POLY_RE = re.compile(
    r"(?i)(?:\b(decrease|increase)\s+interest\s+rates\s+by\s+(\d+)(\+?)\s*bps"
    r"|\b(no\s+change)\s+in\s+fed\s+interest\s+rates)\b.*?\bafter\s+the\s+"
    r"([A-Za-z]+)\s+(\d{4})\s+meeting")


def parse_kalshi(ticker: str) -> tuple[str, str] | None:
    """(meeting 'YYYY-MM', bucket) from a KXFEDDECISION market ticker."""
    m = _KALSHI_RE.match(ticker or "")
    if not m:
        return None
    yy, mon, suffix = m.groups()
    bucket = _KALSHI_BUCKETS.get(suffix)
    month = _MONTHS.get(mon)
    if bucket is None or month is None:
        return None
    return f"20{yy}-{month:02d}", bucket


def parse_polymarket(question: str) -> tuple[str, str] | None:
    """(meeting 'YYYY-MM', bucket) from a Polymarket Fed decision question."""
    if "fed" not in (question or "").lower():
        return None
    m = _POLY_RE.search(question)
    if not m:
        return None
    direction, bps, plus, no_change, month_name, year = m.groups()
    month = _MONTH_NAMES.get(month_name.lower())
    if month is None:
        return None
    if no_change:
        bucket = "hold"
    else:
        size = "50plus" if (plus or int(bps) >= 50) else bps
        bucket = f"{'cut' if direction.lower() == 'decrease' else 'hike'}_{size}"
    return f"{year}-{month:02d}", bucket


def parse(market: BinaryMarket) -> tuple[str, str] | None:
    if market.platform == "kalshi":
        return parse_kalshi(market.market_id)
    return parse_polymarket(market.question)


def kalshi_fee(price: float, fee_rate: float) -> float:
    return fee_rate * price * (1 - price)


@dataclass
class FedSignal:
    meeting: str
    bucket: str
    platform: str
    market_id: str
    kalshi_ticker: str | None
    ask: float
    other_mid: float | None
    lead_days: float
    net_return: float
    stake_usd: float
    detected_at: str = ""

    def __post_init__(self):
        if not self.detected_at:
            self.detected_at = datetime.now(timezone.utc).isoformat()


@dataclass
class BucketState:
    """One (meeting, bucket) across both venues, for the report."""
    meeting: str
    bucket: str
    decision_time: datetime | None
    kalshi: BinaryMarket | None = None
    polymarket: BinaryMarket | None = None


def _mid(m: BinaryMarket | None) -> float | None:
    if m is None:
        return None
    if m.yes_bid is not None and m.yes_ask is not None:
        return (m.yes_bid + m.yes_ask) / 2
    return m.yes_bid if m.yes_bid is not None else m.yes_ask


def group_buckets(markets: list[BinaryMarket]) -> dict[tuple[str, str], BucketState]:
    """Pair the two venues' markets by (meeting, bucket). The decision time is
    the earliest close across venues: Kalshi closes at the decision (2pm ET),
    Polymarket's endDate runs to the following morning."""
    out: dict[tuple[str, str], BucketState] = {}
    for m in markets:
        key = parse(m)
        if key is None:
            continue
        st = out.setdefault(key, BucketState(meeting=key[0], bucket=key[1], decision_time=None))
        if m.platform == "kalshi":
            st.kalshi = m
        elif m.platform == "polymarket":
            st.polymarket = m
        else:
            continue
        if m.close_time and (st.decision_time is None or m.close_time < st.decision_time):
            st.decision_time = m.close_time
    return out


class FedWatcher:
    def __init__(self, cfg: dict):
        self.min_price = float(cfg.get("min_price", 0.90))
        self.max_lead_days = float(cfg.get("max_lead_days", 7))
        # the other venue must carry the same bucket at least this high: a
        # bucket only one venue prices near certainty is a venue quirk, not
        # the reference agreeing
        self.agree_min = float(cfg.get("agree_min", 0.85))
        self.kalshi_fee_rate = float(cfg.get("kalshi_fee_rate", 0.07))
        self.stake_usd = float(cfg.get("stake_usd", 25))

    def net_return(self, platform: str, ask: float) -> float:
        fee = kalshi_fee(ask, self.kalshi_fee_rate) if platform == "kalshi" else 0.0
        return (1 - ask - fee) / ask

    def evaluate(self, markets: list[BinaryMarket],
                 now: datetime | None = None) -> list[FedSignal]:
        """Every (meeting, bucket, venue) the rule fires on right now."""
        now = now or datetime.now(timezone.utc)
        signals: list[FedSignal] = []
        for st in group_buckets(markets).values():
            if st.decision_time is None:
                continue
            lead = (st.decision_time - now).total_seconds() / 86400
            if lead <= 0 or lead > self.max_lead_days:
                continue
            for venue, other in ((st.kalshi, st.polymarket), (st.polymarket, st.kalshi)):
                if venue is None or venue.yes_ask is None or venue.yes_bid is None:
                    continue  # one-sided book: no entry price to record
                if venue.yes_ask < self.min_price:
                    continue
                other_mid = _mid(other)
                if other_mid is None or other_mid < self.agree_min:
                    log.info("fed: %s %s at %.2f on %s but the other venue reads %s — "
                             "not fired", st.meeting, st.bucket, venue.yes_ask,
                             venue.platform,
                             "absent" if other_mid is None else f"{other_mid:.2f}")
                    continue
                signals.append(FedSignal(
                    meeting=st.meeting, bucket=st.bucket, platform=venue.platform,
                    market_id=venue.market_id,
                    kalshi_ticker=st.kalshi.market_id if st.kalshi else None,
                    ask=venue.yes_ask, other_mid=other_mid, lead_days=round(lead, 2),
                    net_return=round(self.net_return(venue.platform, venue.yes_ask), 4),
                    stake_usd=self.stake_usd, detected_at=now.isoformat(),
                ))
        return signals


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def record(conn: sqlite3.Connection, signals: list[FedSignal]) -> int:
    """Persist first fires only: the rule buys once and holds, so the entry is
    the first price it saw, not the last."""
    ensure_schema(conn)
    n = 0
    for s in signals:
        cur = conn.execute(
            "INSERT OR IGNORE INTO fed_signals (detected_at, meeting, bucket, platform,"
            " market_id, kalshi_ticker, ask, other_mid, lead_days, net_return, stake_usd)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (s.detected_at, s.meeting, s.bucket, s.platform, s.market_id, s.kalshi_ticker,
             s.ask, s.other_mid, s.lead_days, s.net_return, s.stake_usd))
        if cur.rowcount:
            n += 1
            log.info("FED rule fired: %s %s YES@%.3f on %s (other venue %.2f, %.1fd out, "
                     "+%.2f%% if it pays, stake $%.0f)", s.meeting, s.bucket, s.ask,
                     s.platform, s.other_mid or 0, s.lead_days, s.net_return * 100,
                     s.stake_usd)
    conn.commit()
    return n


def settle(conn: sqlite3.Connection, results: dict[str, str],
           now: datetime | None = None) -> int:
    """Score open rows from Kalshi settlements (ticker -> 'yes'|'no'). Both
    venues resolve on the same FOMC outcome, so a Polymarket row settles on
    its Kalshi counterpart."""
    ensure_schema(conn)
    ts = (now or datetime.now(timezone.utc)).isoformat()
    n = 0
    for row_id, ticker in conn.execute(
            "SELECT id, kalshi_ticker FROM fed_signals WHERE result IS NULL").fetchall():
        res = results.get(ticker or "")
        if res in ("yes", "no"):
            conn.execute("UPDATE fed_signals SET result=?, settled_at=? WHERE id=?",
                         (res, ts, row_id))
            n += 1
    conn.commit()
    return n


@dataclass
class Record:
    n_fired: int
    n_settled: int
    n_paid: int
    mean_net_return: float      # per $1, over settled rows, losses counted as -1
    breakeven_surprise: float   # loss rate at which the mean settled win nets zero
    surprise_upper95: float | None  # rule of three on zero losses, else observed rate


def scorecard(conn: sqlite3.Connection) -> Record:
    ensure_schema(conn)
    n_fired = conn.execute("SELECT COUNT(*) FROM fed_signals").fetchone()[0]
    rows = conn.execute(
        "SELECT net_return, result FROM fed_signals WHERE result IS NOT NULL").fetchall()
    n = len(rows)
    paid = [r for r, res in rows if res == "yes"]
    realized = [r if res == "yes" else -1.0 for r, res in rows]
    mean_ret = sum(realized) / n if n else 0.0
    win = sum(paid) / len(paid) if paid else 0.0
    breakeven = win / (1 + win) if win > 0 else 0.0
    if n == 0:
        upper = None
    elif len(paid) == n:
        upper = 3 / n
    else:
        upper = (n - len(paid)) / n
    return Record(n_fired=n_fired, n_settled=n, n_paid=len(paid),
                  mean_net_return=round(mean_ret, 4), breakeven_surprise=round(breakeven, 4),
                  surprise_upper95=None if upper is None else round(upper, 4))


def state_lines(markets: list[BinaryMarket], now: datetime | None = None) -> list[str]:
    """Human-readable live state of every open meeting across both venues."""
    now = now or datetime.now(timezone.utc)
    by_meeting: dict[str, list[BucketState]] = {}
    for st in group_buckets(markets).values():
        by_meeting.setdefault(st.meeting, []).append(st)
    lines = []
    for meeting in sorted(by_meeting):
        sts = sorted(by_meeting[meeting], key=lambda s: s.bucket)
        dt = next((s.decision_time for s in sts if s.decision_time), None)
        lead = f"{(dt - now).total_seconds() / 86400:.1f}d out" if dt else "no close time"
        lines.append(f"{meeting}  ({lead})")
        for st in sts:
            k, p = _mid(st.kalshi), _mid(st.polymarket)
            lines.append(f"  {st.bucket:<12} kalshi {'-' if k is None else f'{k:.2f}':>5}"
                         f"   polymarket {'-' if p is None else f'{p:.2f}':>5}")
    return lines


def run_cycle(conn: sqlite3.Connection, watcher: FedWatcher,
              markets: list[BinaryMarket], settled: dict[str, str]) -> tuple[int, int]:
    """Evaluate, record first fires, settle. Returns (new rows, settled rows)."""
    return record(conn, watcher.evaluate(markets)), settle(conn, settled)


def main() -> None:
    import yaml

    from .feeds import KalshiFeed, PolymarketFeed
    from .main import ROOT, open_db

    parser = argparse.ArgumentParser(description="Fed decision rule watcher (records, never trades)")
    parser.add_argument("--report", action="store_true",
                        help="print the settled record and current state without recording")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f) or {}
    conn = open_db(ROOT / cfg.get("storage", {}).get("db_path", "data/arb.db"))
    watcher = FedWatcher(cfg.get("fed_watch", {}))
    kalshi, poly = KalshiFeed(), PolymarketFeed()
    markets = kalshi.series_markets(KALSHI_SERIES) + poly.tagged_markets(POLY_TAG)
    settled = kalshi.series_results(KALSHI_SERIES)
    if not args.report:
        new, done = run_cycle(conn, watcher, markets, settled)
        print(f"recorded {new} new fire(s), settled {done} row(s)")
    print("\n".join(state_lines(markets)) or "no open Fed decision markets on either venue")
    rec = scorecard(conn)
    print(f"\nrecord: fired {rec.n_fired}, settled {rec.n_settled}, paid {rec.n_paid}, "
          f"mean net return {rec.mean_net_return:+.2%}, breakeven surprise rate "
          f"{rec.breakeven_surprise:.2%}, surprise-rate upper bound "
          f"{'n/a' if rec.surprise_upper95 is None else f'{rec.surprise_upper95:.1%}'}")
    for row in conn.execute(
            "SELECT detected_at, meeting, bucket, platform, ask, other_mid, lead_days,"
            " net_return, result FROM fed_signals ORDER BY meeting, bucket, platform"):
        print("  " + " | ".join(str(x) for x in row))


if __name__ == "__main__":
    main()
