#!/usr/bin/env python3
"""
Allocation Advisor — data-driven capital split for the $150 bankroll.

Reads the bot's trade CSV (fee-adjusted PnL per closed trade) and suggests how
to allocate BANKROLL_PER_SPORT across the six (sport, model) buckets.

Usage (on the VPS):
    .venv/bin/python allocation_advisor.py [dry_run_trades_optimal.csv]

Rules this advisor follows — they are the point of the tool:
1. MINIMUM SAMPLE. A bucket with fewer than MIN_TRADES closed trades gets the
   verdict "INSUFFICIENT DATA" and keeps its equal-share allocation. No
   suggestion is made from small samples, ever.
2. NEGATIVE EXPECTANCY = ZERO, NOT LESS. A proven-negative bucket gets $0
   suggested, not a reduced amount. Feeding a losing strategy smaller meals
   still loses money.
3. FRACTIONAL WEIGHTING. Positive buckets are weighted by expectancy but
   smoothed (sqrt) so one hot streak can't grab the whole bankroll.
4. THE ADVISOR ONLY PRINTS. It never edits .env. Reallocation is a human
   decision made between sessions, not something a script hot-swaps mid-run.
"""

from __future__ import annotations

import csv
import math
import os
import sys
from collections import defaultdict

MIN_TRADES = 30
BANKROLL_PER_SPORT = float(os.getenv("BANKROLL_PER_SPORT", "75"))
TOTAL = BANKROLL_PER_SPORT * 2
SPORTS = ("Tennis", "Baseball")
MODELS = ("safe", "base", "risky")

# Column names as written by the bot's trade CSV (exit rows carry pnl).
PNL_CANDIDATES = ("pnl", "net_pnl", "realized_pnl")


def load_closed_trades(path: str):
    buckets = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        pnl_col = next((c for c in PNL_CANDIDATES if c in (reader.fieldnames or [])), None)
        if pnl_col is None:
            print(f"ERROR: none of {PNL_CANDIDATES} found in {path} columns: {reader.fieldnames}")
            sys.exit(1)
        for row in reader:
            action = (row.get("action") or row.get("side") or "").lower()
            if action and "exit" not in action and "close" not in action and "sell" not in action:
                continue
            raw = (row.get(pnl_col) or "").strip()
            if not raw:
                continue
            sport, model = row.get("sport", ""), row.get("model", "")
            if sport in SPORTS and model in MODELS:
                try:
                    buckets[(sport, model)].append(float(raw))
                except ValueError:
                    continue
    return buckets


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "dry_run_trades_optimal.csv"
    if not os.path.exists(path):
        print(f"No trade file at {path}. Run dry-run sessions first.")
        sys.exit(1)

    buckets = load_closed_trades(path)
    equal_share = round(TOTAL / 6.0, 2)

    stats = {}
    for sport in SPORTS:
        for model in MODELS:
            pnls = buckets.get((sport, model), [])
            n = len(pnls)
            expectancy = sum(pnls) / n if n else 0.0
            wins = sum(1 for x in pnls if x > 0)
            stats[(sport, model)] = {
                "n": n, "exp": expectancy, "total": sum(pnls),
                "win": (wins / n * 100.0) if n else 0.0,
            }

    # Eligibility and weights
    weights = {}
    verdicts = {}
    for key, s in stats.items():
        if s["n"] < MIN_TRADES:
            verdicts[key] = f"INSUFFICIENT DATA ({s['n']}/{MIN_TRADES} trades)"
            weights[key] = None  # keeps equal share
        elif s["exp"] <= 0:
            verdicts[key] = "PROVEN NEGATIVE -> $0"
            weights[key] = 0.0
        else:
            verdicts[key] = "POSITIVE"
            weights[key] = math.sqrt(s["exp"])  # smoothed

    proven_keys = [k for k, w in weights.items() if w is not None]
    unproven_keys = [k for k, w in weights.items() if w is None]

    # Capital reserved for unproven buckets stays at equal share (rule 1).
    reserved = equal_share * len(unproven_keys)
    distributable = TOTAL - reserved
    weight_sum = sum(weights[k] for k in proven_keys) or 0.0

    suggestion = {}
    for key in unproven_keys:
        suggestion[key] = equal_share
    for key in proven_keys:
        if weight_sum > 0:
            suggestion[key] = round(distributable * weights[key] / weight_sum, 2)
        else:
            # Everything proven is negative: distributable stays UNDEPLOYED.
            suggestion[key] = 0.0

    print(f"Allocation Advisor — {path}")
    print(f"Total bankroll: ${TOTAL:.0f} | Min sample per bucket: {MIN_TRADES} closed trades\n")
    header = (f"{'bucket':<18} {'trades':>6} {'win%':>6} {'exp/trade':>10} "
              f"{'total pnl':>10} {'now':>7} {'suggested':>10}  verdict")
    print(header)
    print("-" * len(header))
    for sport in SPORTS:
        for model in MODELS:
            key = (sport, model)
            s = stats[key]
            print(f"{sport+'/'+model:<18} {s['n']:>6} {s['win']:>5.0f}% "
                  f"{s['exp']:>+10.4f} {s['total']:>+10.2f} "
                  f"${equal_share:>6.2f} ${suggestion[key]:>9.2f}  {verdicts[key]}")

    undeployed = round(TOTAL - sum(suggestion.values()), 2)
    if undeployed > 0.01:
        print(f"\nUNDEPLOYED: ${undeployed:.2f} — proven-negative buckets release capital,")
        print("and this advisor does not reassign it to unproven ones. Undeployed money")
        print("that isn't losing is a better outcome than deployed money that is.")

    print("\nTo apply: edit BANKROLL_PER_SPORT / per-model splits in .env between")
    print("sessions. This tool never edits configuration itself.")
    if all(s["n"] < MIN_TRADES for s in stats.values()):
        print("\nCurrent verdict: keep $25/$25/$25 everywhere. No bucket has enough")
        print("data to justify anything else yet — that is the honest answer today.")


if __name__ == "__main__":
    main()
