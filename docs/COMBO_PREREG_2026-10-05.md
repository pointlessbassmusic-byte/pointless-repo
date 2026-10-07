# Kalshi combo (parlay) maker economics — pre-registration (2026-10-05)

Committed before any combo trade or settlement data for this test was pulled.

## Hypothesis
Independent work finds combos overpriced relative to their legs:
- Moshrefi, arXiv 2607.14430: parlays priced above their legs.
- Rotowire: Kalshi combo margin 26.5%, vs about 23% at the books.

Takers buying combos therefore lose, and the maker side of combo trades (quoting
through Kalshi's request-for-quote system) earns a positive return after maker
fees.

## Data
- 44 two-minute windows, one per day at a random minute (seed 31), from
  2026-08-20 to 2026-10-02.
- In each window, every trade in a `KXMVE*` market from `/markets/trades`.
- A random 150 trades per window (seed 31) get their market's settlement value.
- Unsettled markets are dropped and counted.

## Measure
- Per trade, maker P&L per contract = (price the taker paid for their side)
  − (payout of the taker's side) − maker fee.
- Maker fee: 0.035·p(1−p) for the primary result (conservative; series fee_type
  `quadratic_with_combo_maker_fees`). Also reported at 0.0175 and 0.
- One observation per trade, clustered by window (day).

## Split and pass
- Discovery: windows Aug 20 – Sep 10. Confirmation: Sep 11 – Oct 2.
- Pass on confirmation: ≥ 1,000 trades, mean > 0, day-clustered t ≥ 2,
  return ≥ 1% of the maker's capital at risk (payout − price).
- Discovery must also be > 0.
- Reported, not required: results by number of legs and by taker side.

## What a pass would and would not mean
- It would mean the winning quotes on combos earned money in this period.
- Replicating it requires our quotes to win the request-for-quote auction, i.e. to
  be at least as tight as the incumbents. A forward test on Kalshi's quoting API
  would still be required.
