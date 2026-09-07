#!/usr/bin/env python3
"""Milestone 1 — real-history backtest adapter (Polymarket sports-bot logs -> ingest schema).

Input:  the history downloader's output directory (polymarket-bot/v2/history_downloader.py):
          <data>/markets.csv           one row per outcome token, with resolution
          <data>/prices/<token>.csv    ts,price  (minute-level, decision-time snapshots)
Output: an ingest.py-schema CSV, and (--report) ScoreBook + Hedge fusion weights over
        the resolved events, exactly as backtest.py does for synthetic worlds.

Honesty rules (mirror PROTOCOL_v1 discipline; this is analysis-orthogonal tooling):
  - market_prob is the LAST recorded price at or before the decision time
    (game_start - lead), never anything after. Events without a pre-decision
    sample are dropped, not backfilled.
  - Decision time must precede game start when game start is known (no in-play).
  - baseline_prob: until the fv_bot sharp-book logs are imported there is no
    independent conventional model here, so --baseline market writes the market
    prob as a PLACEHOLDER (fusion then shows market vs its longshot-corrected
    null only). Do not present baseline rows as an independent expert.
  - One event per condition_id (binary markets carry two complement tokens).

Usage:
  python3 bot_backtest.py --data ../polymarket-bot/v2/data --out bot_events.csv
  python3 bot_backtest.py --data ../polymarket-bot/v2/data --report
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import (Event, HedgeFusion, ScoreBook, brier,  # noqa: E402
                    fit_longshot, longshot_correct)
from ingest import load_events_csv  # noqa: E402


def parse_ts(v) -> float | None:
    if v in (None, ""):
        return None
    s = str(v)
    try:
        return float(s)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def last_price_before(price_path: Path, t: float) -> float | None:
    """Last (ts, price) row with ts <= t. Files are written in time order."""
    best = None
    try:
        with open(price_path) as f:
            for r in csv.DictReader(f):
                ts = parse_ts(r.get("ts"))
                if ts is None or ts > t:
                    continue
                try:
                    best = float(r["price"])
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        return None
    return best


def load_market_rows(data_dir: Path) -> list[dict]:
    with open(data_dir / "markets.csv") as f:
        return list(csv.DictReader(f))


def build_events(data_dir: Path, lead_minutes: float, baseline_mode: str) -> list[dict]:
    rows = load_market_rows(data_dir)
    by_condition: dict[str, dict] = {}
    for r in rows:
        by_condition.setdefault(str(r.get("condition_id")), r)  # one token per market

    out, skipped = [], {"unresolved": 0, "no_time": 0, "no_snapshot": 0}
    for cond, r in by_condition.items():
        rp = r.get("resolved_price")
        try:
            rp = float(rp)
        except (TypeError, ValueError):
            skipped["unresolved"] += 1
            continue
        if rp >= 0.98:
            outcome = 1
        elif rp <= 0.02:
            outcome = 0
        else:
            skipped["unresolved"] += 1
            continue

        game_start = parse_ts(r.get("game_start"))
        end_ts = parse_ts(r.get("end_date"))
        anchor = game_start or end_ts
        if anchor is None:
            skipped["no_time"] += 1
            continue
        decision_t = anchor - lead_minutes * 60.0

        price = last_price_before(data_dir / "prices" / f"{r['token_id']}.csv", decision_t)
        if price is None or not (0.0 < price < 1.0):
            skipped["no_snapshot"] += 1
            continue

        baseline = price if baseline_mode == "market" else None
        out.append({
            "event_id": r.get("slug") or f"cond-{cond[:12]}",
            "domain": "sports",
            "close_time": decision_t,
            "resolve_time": end_ts or anchor,
            "market_prob": round(price, 6),
            "baseline_prob": round(baseline, 6),
            "outcome": outcome,
            "sport": r.get("sport", ""),
            "question": (r.get("question") or "")[:80],
        })
    print(f"adapter: {len(out)} events from {len(by_condition)} markets "
          f"(skipped: {skipped})")
    return out


INGEST_FIELDS = ["event_id", "domain", "close_time", "resolve_time",
                 "market_prob", "baseline_prob", "outcome"]


def write_ingest_csv(events: list[dict], path: Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=INGEST_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(events)
    print(f"wrote {len(events)} rows -> {path}")


def report(ingest_path: Path, baseline_is_placeholder: bool) -> None:
    """ScoreBook + fusion weights over resolved events — the milestone-1 report."""
    events = [e for e in load_events_csv(ingest_path) if e.outcome is not None]
    events.sort(key=lambda e: e.close_time)
    if len(events) < 20:
        print(f"only {len(events)} resolved events — need more history for a meaningful report")
        return

    # Fit the longshot correction on the FIRST half only (fit-on-prior discipline),
    # score on the second half.
    half = len(events) // 2
    a, b = fit_longshot(events[:half])
    scored = events[half:]

    experts = ["market", "market_longshot"] + ([] if baseline_is_placeholder else ["baseline"])
    fusion = HedgeFusion(experts, horizon=len(scored))
    book = ScoreBook(experts + ["fusion"])
    for e in scored:
        probs = {"market": e.market_prob,
                 "market_longshot": longshot_correct(e.market_prob, a, b)}
        if not baseline_is_placeholder:
            probs["baseline"] = e.baseline_prob
        probs["fusion"] = fusion.predict({k: probs[k] for k in experts})
        book.add(e, probs)
        fusion.update({k: probs[k] for k in experts}, e.outcome)

    print(f"\n=== Milestone 1 report: {len(scored)} scored events "
          f"(longshot fit on prior {half}; a={a:.3f} b={b:.3f}) ===")
    for name, row in book.table().items():
        print(f"  {name:16s} n={row['n']:4d}  brier={row['brier']:.4f}  log={row['log']:.4f}")
    w = fusion.weights()
    print("  fusion weights: " + "  ".join(f"{k}={w[k]:.3f}" for k in experts))
    if baseline_is_placeholder:
        print("  NOTE: baseline column is a market placeholder (no independent model yet) —"
              " it was excluded from experts. Import fv_bot sharp-book logs for a real baseline.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default="../polymarket-bot/v2/data",
                    help="history downloader output dir (markets.csv + prices/)")
    ap.add_argument("--out", default="bot_events.csv", help="ingest-schema CSV to write")
    ap.add_argument("--lead-minutes", type=float, default=60.0,
                    help="decision time = game start (or close) minus this")
    ap.add_argument("--baseline", choices=["market"], default="market",
                    help="baseline source (only the placeholder exists until fv_bot logs land)")
    ap.add_argument("--report", action="store_true",
                    help="after writing, run ScoreBook + fusion over resolved events")
    args = ap.parse_args()

    events = build_events(Path(args.data), args.lead_minutes, args.baseline)
    if not events:
        sys.exit("no usable events — run history_downloader.py first")
    write_ingest_csv(events, Path(args.out))
    if args.report:
        report(Path(args.out), baseline_is_placeholder=(args.baseline == "market"))


if __name__ == "__main__":
    main()
