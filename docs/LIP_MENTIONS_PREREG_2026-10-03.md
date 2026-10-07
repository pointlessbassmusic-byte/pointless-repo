# Mentions resting-quote effect — confirmation pre-registration (2026-10-03)

Committed before any data for this test was pulled.

## Hypothesis (generated in LIP_HIST_2026-10-03, exploratory)
On Kalshi "Mentions" markets, continuously resting 100-lot bids on both sides at
the best bid make money on their fills, held to settlement.

## Independent data
- Paid-out liquidity programs in the Mentions category with end dates
  2026-06-01 … 2026-08-31. This window is disjoint from the discovery window
  (Sep 1 – Oct 2).
- Stratified random sample (seed 12) of up to 400 programs; settled markets only.
- Same simulation code as `research/lip_hist/analyze.py`: 1-minute candles, trade
  tape, fills held to settlement. Uses the `/historical/...` endpoints where the
  market settled before the historical cutoff.

## Pass bar (all required)
1. Total fill P&L under **through** > 0, with program-level t ≥ 2 and
   day-clustered t ≥ 2.
2. Total fill P&L under **touch** > 0.
3. Still > 0 under **through** with all `KXTRUMP*` series removed. If only the
   Trump series carry it, report that separately as a narrower pass on Trump
   series only, needing t ≥ 2 on its own.

Rewards are reported but not needed to pass: the claim is about fills.

## If it passes
- It is still not a live strategy: inventory accumulates with continuous quoting.
- Next step is a paper maker on these series (data only), with per-market position
  caps.
