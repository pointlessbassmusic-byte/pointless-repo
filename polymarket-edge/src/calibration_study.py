"""Favorite-longshot calibration on Polymarket's resolved history.

    python -m src.calibration_study --markets 3000 --min-volume 10000

The question this answers with real settled data: when a binary market
trades at price p some hours before it closes, how often does YES actually
happen? If the realized rate sits above p for favorites and below p for
longshots, that gap is a repeatable, model-free edge: buy the favorite, hold
to resolution. If it does not, no amount of modeling on top will find it.

Prices are the last trade at or before each horizon, from the data API's
trade log (the CLOB's own price history is wiped on resolution), normalised
to the YES side. Outcomes come from the final
`outcomePrices`. Only Yes/No markets that actually resolved are used.

Output: a calibration table per horizon (price bucket -> realized YES rate),
a "buy the favorite above X" strategy summary per horizon, and a CSV of every
sampled (market, horizon, price, outcome) row for anyone who wants to slice it
differently.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .http_util import retrying_session

log = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com/markets"
# the CLOB's prices-history is emptied once a market resolves; the data API
# keeps every trade, newest first, and `end=<unix>` bounds the timestamp
TRADES = "https://data-api.polymarket.com/trades"

HORIZONS_H = (1, 6, 24, 72, 168, 720)
BUCKETS = [(lo / 100, (lo + 5) / 100) for lo in range(0, 100, 5)]


def _parse_closed(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.replace(" ", "T")
    if s.endswith("+00"):
        s = s[:-3] + "+00:00"
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except ValueError:
        return None


def fetch_resolved_markets(http, n: int, min_volume: float) -> list[dict]:
    """Closed Yes/No markets with a 1/0 outcome, most-traded first."""
    out, offset, page = [], 0, 100   # Gamma caps a page at 100
    while len(out) < n:
        # Gamma also refuses offsets beyond ~2000, so filter server-side to
        # keep the reachable window full of markets worth sampling
        r = http.get(GAMMA, params={"closed": "true", "limit": page, "offset": offset,
                                    "volume_num_min": min_volume,
                                    "order": "volumeNum", "ascending": "false"}, timeout=60)
        if r.status_code == 422:
            log.info("gamma offset cap reached at %d", offset)
            break
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        for m in rows:
            try:
                outcomes = json.loads(m.get("outcomes") or "[]")
                prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
                tokens = json.loads(m.get("clobTokenIds") or "[]")
            except (ValueError, TypeError):
                continue
            if [o.lower() for o in outcomes] != ["yes", "no"] or len(tokens) != 2:
                continue
            if sorted(prices) != [0.0, 1.0]:
                continue                       # not a clean resolution
            closed = _parse_closed(m.get("closedTime"))
            scheduled = _parse_closed(m.get("endDate"))
            vol = float(m.get("volumeNum") or 0)
            if closed is None or scheduled is None or vol < min_volume:
                continue
            # Horizons must be measured from the *scheduled* end, not the
            # resolution time: a "by <date>" market that resolves YES closes
            # when the event happens, so "h before close" would condition on
            # the outcome. Markets whose scheduled end is a far-future legal
            # placeholder (closed months early) carry no usable calendar.
            if (scheduled - closed).days > 30 or (closed - scheduled).days > 30:
                continue
            out.append({
                "id": m["id"], "question": m.get("question", ""),
                "condition_id": m.get("conditionId", ""),
                "yes_token": tokens[0], "outcome": int(prices[0] == 1.0),
                "closed": closed, "scheduled": scheduled, "volume": vol,
                "neg_risk": bool(m.get("negRisk")),
                "fees": bool(m.get("feesEnabled")),
                "category": (m.get("events") or [{}])[0].get("ticker", "")[:40]
                if isinstance(m.get("events"), list) else "",
            })
        offset += page
        if len(rows) < page:
            break
    return out[:n]


def yes_price_before(http, market: dict, ts: int) -> float | None:
    """YES price of the last trade at or before ts, or None if none happened."""
    r = http.get(TRADES, params={"market": market["condition_id"], "limit": 3, "end": ts},
                 timeout=60)
    r.raise_for_status()
    trades = r.json()
    if not isinstance(trades, list) or not trades:
        return None
    t = max(trades, key=lambda x: x.get("timestamp", 0))
    p = float(t["price"])
    return p if t.get("asset") == market["yes_token"] else 1 - p


def sample(http, market: dict) -> list[dict]:
    rows = []
    end_ts = int(market["scheduled"].timestamp())
    for h in HORIZONS_H:
        p = yes_price_before(http, market, end_ts - h * 3600)
        if p is None or not (0 < p < 1):
            continue
        rows.append({"market_id": market["id"], "horizon_h": h, "price": p,
                     "outcome": market["outcome"], "volume": market["volume"],
                     "neg_risk": market["neg_risk"], "fees": market["fees"],
                     "category": market["category"]})
    return rows


def calibration(rows: list[dict]) -> dict[int, list[tuple]]:
    """horizon -> [(lo, hi, n, mean_price, yes_rate)]"""
    by_h: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        b = min(int(r["price"] * 20), 19)
        by_h[r["horizon_h"]][b].append(r)
    out = {}
    for h, buckets in by_h.items():
        table = []
        for b in range(20):
            rs = buckets.get(b, [])
            if not rs:
                continue
            table.append((BUCKETS[b][0], BUCKETS[b][1], len(rs),
                          sum(r["price"] for r in rs) / len(rs),
                          sum(r["outcome"] for r in rs) / len(rs)))
        out[h] = table
    return out


def favorite_strategy(rows: list[dict], threshold: float) -> dict[int, dict]:
    """Buy whichever side trades above `threshold`, hold to resolution.

    Return per $1 staked = payout/price - 1. Uses the traded price, so real
    fills pay the spread on top; treat the numbers as an upper bound."""
    by_h: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        p, y = r["price"], r["outcome"]
        if p >= threshold:
            by_h[r["horizon_h"]].append(y / p - 1)
        elif 1 - p >= threshold:
            by_h[r["horizon_h"]].append((1 - y) / (1 - p) - 1)
    out = {}
    for h, rets in by_h.items():
        n = len(rets)
        mean = sum(rets) / n
        wins = sum(1 for x in rets if x > 0)
        var = sum((x - mean) ** 2 for x in rets) / max(1, n - 1)
        out[h] = {"n": n, "mean_return": mean, "hit_rate": wins / n,
                  "stderr": (var / n) ** 0.5 if n > 1 else float("nan")}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--markets", type=int, default=3000)
    ap.add_argument("--min-volume", type=float, default=10_000)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="docs/calibration_rows.csv")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    http = retrying_session()
    markets = fetch_resolved_markets(http, args.markets, args.min_volume)
    log.info("%d resolved Yes/No markets with volume >= %.0f", len(markets), args.min_volume)

    rows: list[dict] = []
    failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(sample, http, m): m for m in markets}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                rows.extend(f.result())
            except Exception:  # noqa: BLE001 — one dead market must not sink the study
                failed += 1
            if i % 250 == 0:
                log.info("histories: %d/%d (%d failed)", i, len(markets), failed)
    log.info("%d sampled rows from %d markets (%d histories failed)",
             len(rows), len(markets), failed)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["empty"])
        w.writeheader()
        w.writerows(rows)

    print(f"\nmarkets: {len(markets)}  rows: {len(rows)}  "
          f"fees-enabled markets: {sum(1 for m in markets if m['fees'])}  "
          f"neg-risk markets: {sum(1 for m in markets if m['neg_risk'])}")
    for h, table in sorted(calibration(rows).items()):
        print(f"\n--- {h}h before close: price bucket -> realized YES rate ---")
        print(f"{'bucket':<12}{'n':>6}{'price':>8}{'yes':>8}{'gap':>8}")
        for lo, hi, n, mp, yr in table:
            print(f"{lo:.2f}-{hi:.2f}  {n:>6}{mp:>8.3f}{yr:>8.3f}{yr - mp:>+8.3f}")

    print("\n--- buy the favorite, hold to resolution (return per $1, gross of spread) ---")
    print(f"{'horizon':<9}{'thresh':>7}{'n':>7}{'mean ret':>10}{'stderr':>8}{'hit':>7}")
    for thr in (0.80, 0.90, 0.95):
        for h, s in sorted(favorite_strategy(rows, thr).items()):
            print(f"{h:<9}{thr:>7.2f}{s['n']:>7}{s['mean_return']:>+10.4f}"
                  f"{s['stderr']:>8.4f}{s['hit_rate']:>7.3f}")
    print(f"\nrows written to {out}")


if __name__ == "__main__":
    sys.exit(main())
