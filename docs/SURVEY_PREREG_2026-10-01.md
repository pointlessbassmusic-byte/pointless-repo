# Kalshi exchange-wide strategy survey — pre-registration (2026-10-01)

Written and committed BEFORE any result was computed. The analysis must follow
these rules; anything else is labelled exploratory.

## Data
- Settled Kalshi markets (`mve_filter=exclude`, volume > 0) closing on 20 sampled
  days: day offsets 2, 8, …, 116 before 2026-10-01T00:00Z.
- Per series: up to 120 events spread evenly over time; per event up to 25 markets
  chosen at random (seed 7) — not by volume, which would leak the price path.
- Candlesticks per market: 1-min if lifetime ≤ 2h, 60-min if ≤ 7d, else daily.
  Quotes are forward-filled from the last candle at or before entry time.
- Known bias, disclosed: the volume > 0 filter uses end-of-life information.

## Entry rule (no look-ahead)
- Clock: `open_time` → `expected_expiration_time` (scheduled, not actual, close;
  the actual close of early-closing markets leaks the outcome).
- Entry at elapsed fraction f ∈ {0.50, 0.75, 0.90, 0.98}. No entry if the market
  had already closed by then.
- Quote valid only if 0 < bid < ask < 1, ask − bid ≤ 0.10, mid ∈ [0.03, 0.97].
- Momentum = mid(f) − mid(f − 0.25): up (> +0.02), down (< −0.02), flat, or any.

## Grid (every combination, plus its inverse)
exec {taker, maker_ub} × side {YES, NO} × mid bucket {[.03,.15), [.15,.35),
[.35,.65), [.65,.85), [.85,.97]} × f (4) × momentum (4) = 320 cells, evaluated per
Kalshi category and per series (sub-field). YES vs NO in the same cell is the
inverse strategy.
- taker: buy at ask (YES) / 1 − bid (NO); fee 0.07 × series fee_multiplier × p(1−p).
- maker_ub: buy at bid (YES) / 1 − ask (NO), filled with certainty, no adverse
  selection; maker fee 0.0175 × mult × p(1−p) only on `quadratic_with_maker_fees`
  series. This is an UPPER BOUND; a maker cell that fails is dead, a maker cell
  that passes still needs trade-tape fill verification.
- Settle at `settlement_value_dollars`. P&L per contract.
- Unit: one observation per event per cell (contracts in the same event and cell
  averaged).

## Split and pass bar
- Discovery: events closing on offsets ≥ 62 days. Confirmation: offsets ≤ 56.
- Discovery pass: n ≥ 30 events, mean P&L > 0, event-level t ≥ 3.
- Confirmation pass (same cell, untouched data): n ≥ 30, mean P&L > 0,
  event-level t ≥ 2, day-clustered t ≥ 2 over ≥ 5 days, ROI ≥ 1% of cost.
- Report the number of cells tested and the expected number of false passes.
- Survivors are candidates for a forward paper test, not for live money.

## Amendment 1 (before any candle data was fetched or analysed)
Per-series event cap raised 40 → 120: with 40, no series could reach n ≥ 30 per
half, so the sub-field (series) level would have been untestable.

## Amendment 2 (data-source fix, after a first run)
The live `/markets` listing returns nothing for settlements before ~2026-07-26;
those are served by `/historical/markets` (cutoff 2026-08-01). The first run
therefore had only 2 discovery days (offsets 62, 68) instead of 10. Fix: each
sampled day is listed from both endpoints, unioned by ticker. Design and bars
unchanged.
Disclosure: the first (broken-split) run printed confirmation-half statistics for
the cells that passed its discovery step. The confirmation half is therefore no
longer untouched for those cells. Any survivor of the corrected run must be
re-tested on markets that settle after 2026-10-01 before it counts.
