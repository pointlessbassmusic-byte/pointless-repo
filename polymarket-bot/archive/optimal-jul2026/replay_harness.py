#!/usr/bin/env python3
"""
Record-and-replay harness for the Polymarket sports bot.

Replays REAL recorded quotes (real_ticks.csv, produced by the bot's tick
recorder during dry runs) through the entry/exit logic under many parameter
sets, and reports fee-adjusted results.

Usage (on the VPS):
    .venv/bin/python replay_harness.py [real_ticks.csv]

Honesty rules built into this harness — do not remove them:
1. FIT/HOLDOUT SPLIT. The tick stream is split by time: parameters are ranked
   on the first 60% (fit) and the number that matters is their performance on
   the last 40% (holdout), which they were never tuned on. In-sample-only
   results are overfitting with extra steps.
2. SMALL-SAMPLE WARNING. Any cell with < 30 closed trades is flagged; its
   numbers are noise, not signal.
3. FEES ALWAYS ON. Every simulated round trip pays the p*(1-p) taker fee both
   legs. There is no way to switch this off.
4. ENTRIES PAY THE ASK, EXITS RECEIVE THE BID — spread cost is real because
   the recorded data carries real spreads.
"""

from __future__ import annotations

import csv
import itertools
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime

SPORTS_FEE_RATE = 0.05
NOTIONAL_PER_TRADE = 10.0
COOLDOWN_SECONDS = 60.0
FIT_FRACTION = 0.60
MIN_TRADES_FOR_SIGNAL = 30


def taker_fee(price: float, shares: float) -> float:
    p = min(1.0, max(0.0, price))
    return SPORTS_FEE_RATE * p * (1.0 - p) * shares


@dataclass(frozen=True)
class Params:
    entry_min_mid: float
    entry_max_mid: float
    max_spread: float
    move_trigger: float
    lookback: int
    take_profit: float
    stop_loss: float
    reversal_mult: float
    max_hold_seconds: float

    def label(self) -> str:
        return (
            f"band={self.entry_min_mid:.2f}-{self.entry_max_mid:.2f} "
            f"spr<={self.max_spread:.3f} trig={self.move_trigger:.3f} "
            f"tp={self.take_profit:.3f} sl={self.stop_loss:.3f} "
            f"rev={self.reversal_mult:.1f} hold={int(self.max_hold_seconds)}s"
        )


# Default sweep grid. Small on purpose: 2*2*2*3*2 = 48 combos. A huge grid on a
# small dataset guarantees a lucky-looking winner; keep the grid modest and let
# the holdout column do the judging.
def default_grid() -> list[Params]:
    grid = []
    for band in ((0.55, 0.90), (0.40, 0.90)):
        for max_spread in (0.014, 0.024):
            for trigger in (0.004, 0.008):
                for tp, sl in ((0.032, 0.020), (0.042, 0.026), (0.055, 0.030)):
                    for hold in (600.0, 1200.0):
                        grid.append(Params(
                            entry_min_mid=band[0], entry_max_mid=band[1],
                            max_spread=max_spread, move_trigger=trigger,
                            lookback=8, take_profit=tp, stop_loss=sl,
                            reversal_mult=1.0, max_hold_seconds=hold,
                        ))
    return grid


def load_ticks(path: str) -> dict[str, list[tuple[float, float, float]]]:
    """token_id -> [(epoch_seconds, bid, ask), ...] sorted by time."""
    streams: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            bid_raw, ask_raw = row.get("best_bid", ""), row.get("best_ask", "")
            if not bid_raw or not ask_raw:
                continue
            bid, ask = float(bid_raw), float(ask_raw)
            if bid <= 0 or ask <= 0 or ask <= bid:
                continue  # skip invalid/crossed samples
            ts = datetime.fromisoformat(row["timestamp_utc"]).timestamp()
            streams[row["token_id"]].append((ts, bid, ask))
    for token in streams:
        streams[token].sort(key=lambda x: x[0])
    return dict(streams)


@dataclass
class Result:
    trades: int = 0
    wins: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0

    def expectancy(self) -> float:
        return self.total_pnl / self.trades if self.trades else 0.0

    def win_rate(self) -> float:
        return self.wins / self.trades * 100.0 if self.trades else 0.0


def replay_stream(samples: list[tuple[float, float, float]], p: Params) -> Result:
    result = Result()
    mids: deque[tuple[float, float]] = deque(maxlen=60)
    position = None  # (entry_ts, entry_ask, qty, entry_fee)
    cooldown_until = 0.0

    for ts, bid, ask in samples:
        mid = (bid + ask) / 2.0
        spread = ask - bid
        mids.append((ts, mid))

        if position is not None:
            entry_ts, entry_ask, qty, entry_fee = position
            price_pnl = bid - entry_ask
            hold = ts - entry_ts
            round_trip = taker_fee(entry_ask, 1.0) + taker_fee(bid, 1.0)
            required_tp = max(p.take_profit, round_trip + 0.01)

            reason = None
            if price_pnl >= required_tp:
                reason = "tp"
            elif price_pnl <= -p.stop_loss:
                reason = "sl"
            elif hold >= p.max_hold_seconds:
                reason = "time"
            elif hold >= 30 and len(mids) > 4:
                recent = mids[-1][1] - mids[-5][1]
                if recent <= -(p.move_trigger * p.reversal_mult):
                    reason = "reversal"

            if reason:
                exit_fee = taker_fee(bid, qty)
                pnl = qty * (bid - entry_ask) - entry_fee - exit_fee
                result.trades += 1
                result.total_pnl += pnl
                result.total_fees += entry_fee + exit_fee
                if pnl > 0:
                    result.wins += 1
                position = None
                cooldown_until = ts + COOLDOWN_SECONDS
            continue

        # Entry evaluation
        if ts < cooldown_until or len(mids) <= p.lookback:
            continue
        if spread > p.max_spread:
            continue
        if mid < p.entry_min_mid or mid > p.entry_max_mid:
            continue
        momentum = mids[-1][1] - mids[-1 - p.lookback][1]
        if momentum >= p.move_trigger:
            qty = NOTIONAL_PER_TRADE / ask
            entry_fee = taker_fee(ask, qty)
            position = (ts, ask, qty, entry_fee)

    # Force-close any open position at the final bid (mirrors bot shutdown).
    if position is not None and samples:
        entry_ts, entry_ask, qty, entry_fee = position
        final_bid = samples[-1][1]
        exit_fee = taker_fee(final_bid, qty)
        pnl = qty * (final_bid - entry_ask) - entry_fee - exit_fee
        result.trades += 1
        result.total_pnl += pnl
        result.total_fees += entry_fee + exit_fee
        if pnl > 0:
            result.wins += 1
    return result


def merge(a: Result, b: Result) -> Result:
    return Result(a.trades + b.trades, a.wins + b.wins,
                  a.total_pnl + b.total_pnl, a.total_fees + b.total_fees)


def split_streams(streams: dict[str, list], fraction: float):
    """Time-based split across the whole recording, not per-token."""
    all_ts = sorted(ts for samples in streams.values() for ts, _, _ in samples)
    if not all_ts:
        return {}, {}
    cut = all_ts[int(len(all_ts) * fraction) - 1]
    fit, holdout = {}, {}
    for token, samples in streams.items():
        f = [s for s in samples if s[0] <= cut]
        h = [s for s in samples if s[0] > cut]
        if len(f) > 10:
            fit[token] = f
        if len(h) > 10:
            holdout[token] = h
    return fit, holdout


def evaluate(streams: dict[str, list], p: Params) -> Result:
    total = Result()
    for samples in streams.values():
        total = merge(total, replay_stream(samples, p))
    return total


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "real_ticks.csv"
    streams = load_ticks(path)
    n_samples = sum(len(s) for s in streams.values())
    print(f"Loaded {n_samples} valid samples across {len(streams)} tokens from {path}")
    if n_samples < 5000:
        print("WARNING: thin dataset. Run more dry-run sessions before trusting any row below.")

    fit_streams, holdout_streams = split_streams(streams, FIT_FRACTION)
    print(f"Fit window tokens: {len(fit_streams)} | Holdout window tokens: {len(holdout_streams)}\n")

    rows = []
    for p in default_grid():
        fit_result = evaluate(fit_streams, p)
        holdout_result = evaluate(holdout_streams, p)
        rows.append((p, fit_result, holdout_result))

    # Rank by FIT expectancy, judge by HOLDOUT expectancy.
    rows.sort(key=lambda r: r[1].expectancy(), reverse=True)

    header = (f"{'params':<78} | {'fit n':>5} {'fit exp':>8} {'fit win%':>8} "
              f"| {'hold n':>6} {'hold exp':>9} {'hold win%':>9} {'flag':>6}")
    print(header)
    print("-" * len(header))
    for p, fit_r, hold_r in rows[:15]:
        flag = "NOISE" if hold_r.trades < MIN_TRADES_FOR_SIGNAL else ""
        print(f"{p.label():<78} | {fit_r.trades:>5} {fit_r.expectancy():>8.4f} "
              f"{fit_r.win_rate():>7.0f}% | {hold_r.trades:>6} "
              f"{hold_r.expectancy():>9.4f} {hold_r.win_rate():>8.0f}% {flag:>6}")

    print("\nHow to read this: pick candidates by the FIT columns, but believe only")
    print("the HOLDOUT columns. A parameter set is worth promoting to the live .env")
    print(f"only if holdout expectancy is positive on >= {MIN_TRADES_FOR_SIGNAL} trades.")
    print("If nothing clears that bar, the strategy doesn't work on this data —")
    print("that is a finding, not a failure of the harness.")


if __name__ == "__main__":
    main()
