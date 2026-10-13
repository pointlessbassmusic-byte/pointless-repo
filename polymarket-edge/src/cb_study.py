"""Does the Fed rule generalise to other central banks? Resolved Polymarket data.

    python -m src.cb_study                 # every bank, writes the row CSV
    python -m src.cb_study --banks fed ecb # a subset

The Fed rule (RESOLVED_MARKET_STUDY Result 3, arb-scanner `fed_watch.py`):
within 7 days of a meeting, buy the decision bucket at >= 0.90 and hold. 18
meetings, 0 losses — which bounds the surprise rate only at ~16% (rule of
three) against a ~2.4% breakeven. Every other central bank with a deep
OIS/futures market pricing the same decision is the same class of event, so
pooling them is how the surprise-rate bound tightens, and how the recorder
gets more than eight chances a year.

Method, per decision event (mutually exclusive rate buckets, one resolves
YES):
* anchor = 00:00 UTC of the event's `endDate` date. Polymarket usually
  dates these events on the announcement day, and every bank here announces
  after 00:30 UTC (RBNZ/BoK/PBoC ~01:00, BoJ ~03:00, RBA 03:30-04:30, the
  rest later). Some events are dated the next morning instead (ECB Oct 2026
  03:59Z, RBA Nov 2026 04:59Z), which puts the anchor up to ~1 day after the
  decision; the 24 h snapshot can then be post-decision. Re-scoring without
  the 24 h and 48 h snapshots leaves the G10 result unchanged (57 fires, 0
  losses) and only removes a loss elsewhere, so no win in the table comes
  from that. Events resolved before their anchor are dropped as misdated.
* at each horizon h in HORIZONS_H before the anchor, the YES price of every
  bucket is its last trade at or before that time (data API; the CLOB's
  history is wiped on resolution); the favourite is the highest.
* first fire: scanning from the earliest horizon, the first time the
  favourite is >= the threshold is the entry — what the recorder would have
  stored. Cost = price + 1c spread + the market's own taker fee
  (rate x p x (1-p); 0 when fees are off). Return = payoff / cost - 1.

Output per bank and pooled: meetings, fires, losses, mean return, and the
rule-of-three upper bound on the loss rate. The 0.80-0.90 band is reported
separately and is not a rule (CLAUDE.md: do not lower the threshold).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .calibration_study import _parse_closed
from .http_util import retrying_session

log = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
TRADES = "https://data-api.polymarket.com/trades"
HORIZONS_H = (168, 120, 72, 48, 24)          # earliest first: first-fire order
SPREAD = 0.01
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")

# bank -> (search phrase with {m} for the month, slug prefixes that identify it)
BANKS: dict[str, tuple[str, tuple[str, ...]]] = {
    "fed": ("Fed decision in {m}", ("fed-decision-in-",)),
    "ecb": ("ECB interest rates {m}", ("ecb-interest-rates-", "ecb-decision-")),
    "boe": ("Bank of England decision in {m}", ("bank-of-england-decision-",)),
    "boj": ("Bank of Japan decision in {m}", ("bank-of-japan-decision-",)),
    "boc": ("Bank of Canada decision in {m}", ("bank-of-canada-decision-",)),
    "rba": ("Reserve Bank of Australia decision in {m}",
            ("reserve-bank-of-australia-decision-",)),
    "rbnz": ("Reserve Bank of New Zealand decision in {m}",
             ("reserve-bank-of-new-zealand-decision-",)),
    "bok": ("Bank of Korea decision in {m}", ("bank-of-korea-decision-",)),
    "brazil": ("Bank of Brazil decision in {m}", ("bank-of-brazil-decision-",)),
    "mexico": ("Bank of Mexico decision in {m}", ("bank-of-mexico-decision-",)),
    "colombia": ("Central Bank of Colombia decision in {m}",
                 ("central-bank-of-colombia-decision-",)),
    "russia": ("Bank of Russia decision in {m}", ("bank-of-russia-decision-",)),
    "israel": ("Bank of Israel decision in {m}", ("bank-of-israel-decision-",)),
    "sarb": ("South Africa Reserve Bank decision in {m}",
             ("south-africa-reserve-bank-decision-",)),
    "pboc": ("People's Bank of China decision in {m}",
             ("peoples-bank-of-china-",)),
}
# Currencies with deep, liquid OIS/futures markets pricing the decision (the
# property CLAUDE.md names for the reference class). Fixed here before the
# split is read; the split itself was added after the pooled losses were seen,
# so treat it as a hypothesis for the forward record, not a finding.
G10 = {"fed", "ecb", "boe", "boj", "boc", "rba", "rbnz"}
# only the per-meeting decision event, never "rate cut in 2026", parlays,
# dissent counts or "by <date>" markets
_NOT_DECISION = re.compile(r"(-in-20\d\d$|parlay|dissent|hike-in-|cut-in-|emergency|by-)")


def bank_of(slug: str) -> Optional[str]:
    for bank, (_, prefixes) in BANKS.items():
        if slug.startswith(prefixes) and not _NOT_DECISION.search(slug):
            return bank
    return None


def taker_fee(p: float, rate: float) -> float:
    return rate * p * (1 - p)


def fee_rate(market: dict) -> float:
    """The market's own taker rate; 0.05 when fees are on but Gamma is silent."""
    if not market.get("feesEnabled"):
        return 0.0
    sched = market.get("feeSchedule") or {}
    try:
        return float(sched.get("rate", 0.05))
    except (TypeError, ValueError):
        return 0.05


def anchor_ts(end_date: str) -> int:
    d = datetime.fromisoformat(end_date.replace("Z", "+00:00")).astimezone(timezone.utc)
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def first_fire(snapshots: list[tuple[int, Optional[float], Optional[int]]],
               lo: float, hi: float = 1.0) -> Optional[tuple[int, float, int]]:
    """snapshots: (horizon_h, favourite price, favourite bucket index), earliest
    first. The first horizon whose favourite price is in [lo, hi)."""
    for h, p, i in snapshots:
        if p is not None and lo <= p < hi:
            return h, p, i
    return None


def entry_return(p: float, won: bool, rate: float, spread: float = SPREAD) -> float:
    cost = p + spread + taker_fee(p, rate)
    return (1.0 if won else 0.0) / cost - 1.0


def rule_of_three(n: int) -> Optional[float]:
    return 3.0 / n if n else None


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def discover(http, banks: list[str]) -> list[dict]:
    """Closed decision events for the given banks, by month-name search plus
    the Global-Rates tag (the search endpoint caps its results)."""
    slugs: dict[str, str] = {}
    for bank in banks:
        phrase = BANKS[bank][0]
        for m in MONTHS:
            r = http.get(f"{GAMMA}/public-search", params={
                "q": phrase.format(m=m), "events_status": "closed",
                "limit_per_type": 50}, timeout=60)
            if r.status_code != 200:
                continue
            for e in r.json().get("events") or []:
                if bank_of(e.get("slug", "")) == bank:
                    slugs[e["slug"]] = bank
    for tag in ("Global-Rates", "fed-rates", "ecb"):
        for off in range(0, 1000, 100):
            r = http.get(f"{GAMMA}/events", params={
                "tag_slug": tag, "closed": "true", "limit": 100, "offset": off}, timeout=60)
            rows = r.json() if r.status_code == 200 else []
            for e in rows:
                b = bank_of(e.get("slug", ""))
                if b in banks:
                    slugs[e["slug"]] = b
            if len(rows) < 100:
                break
    events = []
    for slug, bank in sorted(slugs.items()):
        r = http.get(f"{GAMMA}/events", params={"slug": slug}, timeout=60)
        if r.status_code == 200 and r.json():
            e = r.json()[0]
            e["_bank"] = bank
            events.append(e)
    return events


def buckets(event: dict) -> Optional[list[dict]]:
    """Resolved Yes/No bucket markets with exactly one YES, else None."""
    out = []
    for m in event.get("markets") or []:
        try:
            outcomes = [o.lower() for o in json.loads(m.get("outcomes") or "[]")]
            prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
            tokens = json.loads(m.get("clobTokenIds") or "[]")
        except (ValueError, TypeError):
            return None
        if outcomes != ["yes", "no"] or len(tokens) != 2 or sorted(prices) != [0.0, 1.0]:
            continue
        out.append({"question": m.get("question", ""), "condition_id": m["conditionId"],
                    "yes_token": str(tokens[0]), "won": prices[0] == 1.0,
                    "rate": fee_rate(m)})
    if len(out) < 2 or sum(b["won"] for b in out) != 1:
        return None
    return out


def yes_price_before(http, bucket: dict, ts: int) -> tuple[Optional[float], Optional[int]]:
    r = http.get(TRADES, params={"market": bucket["condition_id"], "limit": 3, "end": ts},
                 timeout=60)
    trades = r.json() if r.status_code == 200 else []
    if not isinstance(trades, list) or not trades:
        return None, None
    t = max(trades, key=lambda x: x.get("timestamp", 0))
    p = float(t["price"])
    p = p if str(t.get("asset")) == bucket["yes_token"] else 1 - p
    return p, ts - int(t["timestamp"])


def snapshot_event(http, event: dict) -> Optional[dict]:
    bks = buckets(event)
    if not bks or not event.get("endDate"):
        return None
    anchor = anchor_ts(event["endDate"])
    closed = _parse_closed(event.get("closedTime"))
    if closed is not None and closed.timestamp() < anchor:
        return None                              # resolved before its own date
    snaps = []
    for h in HORIZONS_H:
        ts = anchor - h * 3600
        best = (None, None, None)
        for i, b in enumerate(bks):
            p, age = yes_price_before(http, b, ts)
            if p is not None and (best[0] is None or p > best[0]):
                best = (p, i, age)
        snaps.append({"h": h, "p": best[0], "i": best[1], "age_h": None if best[2] is None
                      else best[2] / 3600})
    return {"bank": event["_bank"], "slug": event["slug"], "date": event["endDate"][:10],
            "volume": float(event.get("volume") or 0), "buckets": bks, "snaps": snaps}


def score(ev: dict, lo: float, hi: float = 1.0, max_age_h: Optional[float] = None) -> Optional[dict]:
    snaps = [(s["h"], s["p"] if max_age_h is None or (s["age_h"] or 0) <= max_age_h else None,
              s["i"]) for s in ev["snaps"]]
    f = first_fire(snaps, lo, hi)
    if f is None:
        return None
    h, p, i = f
    b = ev["buckets"][i]
    return {"bank": ev["bank"], "slug": ev["slug"], "date": ev["date"], "h": h, "p": p,
            "won": b["won"], "ret": entry_return(p, b["won"], b["rate"]),
            "bucket": b["question"]}


def dedupe(events: list[dict], days: int = 7) -> list[dict]:
    """One event per meeting: the same bank listed twice within `days` (e.g.
    an "August" and a "September" event for one late-August meeting) keeps
    the higher-volume listing."""
    out: list[dict] = []
    for e in sorted(events, key=lambda e: (e["bank"], e["date"])):
        prev = out[-1] if out else None
        if (prev and prev["bank"] == e["bank"]
                and abs((datetime.fromisoformat(e["date"])
                         - datetime.fromisoformat(prev["date"])).days) <= days):
            if e["volume"] > prev["volume"]:
                out[-1] = e
            continue
        out.append(e)
    return out


def summarise(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    rets = [r["ret"] for r in rows]
    mu = sum(rets) / n
    se = math.sqrt(sum((x - mu) ** 2 for x in rets) / (n - 1) / n) if n > 1 else 0.0
    losses = sum(1 for r in rows if not r["won"])
    return {"n": n, "losses": losses, "mean": mu, "se": se,
            "avg_p": sum(r["p"] for r in rows) / n,
            "bound": rule_of_three(n) if losses == 0 else None}


def report(events: list[dict], max_age_h: Optional[float] = None) -> str:
    lines = []
    for label, lo, hi in (("rule: favourite >= 0.90", 0.90, 1.0),
                          ("band 0.80-0.90 (not a rule)", 0.80, 0.90)):
        lines.append(f"\n{label}, first fire within 7 days, 1c spread + taker fee"
                     + (f", prints <= {max_age_h:.0f}h old" if max_age_h else ""))
        lines.append(f"{'bank':<12}{'meetings':>9}{'fired':>7}{'losses':>8}{'mean ret':>10}"
                     f"{'se':>8}{'avg p':>7}{'loss<=':>8}")
        all_rows = []
        for bank in BANKS:
            evs = [e for e in events if e["bank"] == bank]
            if not evs:
                continue
            rows = [r for r in (score(e, lo, hi, max_age_h) for e in evs) if r]
            all_rows += rows
            lines.append(_fmt_row(bank, len(evs), summarise(rows)))
        for name, keep in (("ex-fed", lambda b: b != "fed"),
                           ("g10 ex-fed", lambda b: b in G10 and b != "fed"),
                           ("other", lambda b: b not in G10),
                           ("all", lambda b: True)):
            lines.append(_fmt_row(name, sum(keep(e["bank"]) for e in events),
                                  summarise([r for r in all_rows if keep(r["bank"])])))
        if lo >= 0.90:
            for cut in (0.95,):
                for name, sel in ((f"p<{cut}", lambda r: r["p"] < cut),
                                  (f"p>={cut}", lambda r: r["p"] >= cut)):
                    lines.append(_fmt_row(name, 0, summarise([r for r in all_rows if sel(r)])))
        losers = [r for r in all_rows if not r["won"]]
        for r in losers:
            lines.append(f"  LOSS {r['bank']} {r['date']} {r['h']}h p={r['p']:.3f} "
                         f"{r['bucket'][:70]}")
    return "\n".join(lines)


def _fmt_row(name: str, meetings: int, s: dict) -> str:
    if not s.get("n"):
        return f"{name:<12}{meetings:>9}{0:>7}"
    bound = f"{s['bound']:.1%}" if s.get("bound") is not None else "-"
    return (f"{name:<12}{meetings:>9}{s['n']:>7}{s['losses']:>8}{s['mean']:>+10.2%}"
            f"{s['se']:>8.2%}{s['avg_p']:>7.3f}{bound:>8}")


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--banks", nargs="*", default=list(BANKS))
    ap.add_argument("--out", default="data/cb_study_rows.json")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--cached", action="store_true",
                    help="re-score the rows saved by the last run, no network")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    if args.cached:
        snaps = dedupe([e for e in json.loads(Path(args.out).read_text())
                        if e["bank"] in args.banks])
        print(report(snaps))
        print(report(snaps, max_age_h=24))
        return
    http = retrying_session()
    events = discover(http, args.banks)
    log.info("%d decision events", len(events))
    with ThreadPoolExecutor(args.workers) as ex:
        snaps = dedupe([s for s in ex.map(lambda e: snapshot_event(http, e), events) if s])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(snaps))
    print(f"{len(snaps)} resolved decision events with clean buckets")
    print(report(snaps))
    print(report(snaps, max_age_h=24))
    with open(Path(args.out).with_suffix(".csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["bank", "date", "slug", "horizon_h", "fav_price", "print_age_h", "fav_won"])
        for e in snaps:
            for s in e["snaps"]:
                won = None if s["i"] is None else e["buckets"][s["i"]]["won"]
                w.writerow([e["bank"], e["date"], e["slug"], s["h"], s["p"], s["age_h"], won])


if __name__ == "__main__":
    main()
