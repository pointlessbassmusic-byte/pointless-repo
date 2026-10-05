# Kalshi 15-minute crypto Up/Down — strategies from three X posts — pre-registration (2026-10-05)

Committed before any candle, trade or spot data for this test was pulled.

## Sources
- @Dan1ro0: a DMI/ADX trend system on Polymarket's BTC Up/Down markets.
- @RetroValix: a spot fair-value model plus complete-set hedging.
- The wallets they credit both turned out to be two-sided makers that merge
  complete sets (docs/ARTICLES_CRYPTO_2026-10-05.md).
- @0xNevsky (memecoin "moonbag" exits): not a prediction-market strategy; out of
  scope.

## Venue
- Kalshi KXBTC15M, KXETH15M, KXSOL15M: US-legal; quadratic fees, so makers pay
  nothing.
- YES if the 60-second BRTI average at close ≥ the 60-second average at open
  (= `floor_strike`).
- Settled markets closing 2026-08-21 … 2026-10-04.
- Discovery = closes before 2026-09-13; confirmation = from 2026-09-13.

## Data
- Kalshi 1-minute candles per market (yes bid / ask close).
- Spot: Coinbase 1-minute candles (BTC-USD, ETH-USD, SOL-USD) as a proxy for
  BRTI, which is a multi-exchange index.
- S3 also uses the Kalshi trade tape for a random sample of 600 markets
  (200 per asset, seed 21).

## Strategies (taker fee 0.07·p(1−p) on taker legs; one entry per market)
**S1 DMI/ADX (Wilder, 14-period, on 1-min spot bars up to the entry minute).**
At entry minute k ∈ {5, 10} after open: if ADX ≥ 25, buy YES at the ask when
+DI > −DI, or NO at 1 − bid when −DI > +DI; otherwise no trade. Its inverse
(the opposite side) is reported.

**S2 spot fair value.**
- fair = Φ( ln(S_t / K) / (σ·√τ) ), where:
  - S_t = the spot close at minute t;
  - K = `floor_strike`;
  - σ = the standard deviation of the last 60 one-minute log returns;
  - τ = minutes to close.
- For t = 1 … 13, take the first minute with fair − ask − fee ≥ 0.03 (buy YES),
  or (1 − fair) − (1 − bid) − fee ≥ 0.03 (buy NO).
- Execution price = the Kalshi quote at **t + 1 minute**. This is primary, because
  it allows for the latency of a public-data bot. Same-minute execution is
  secondary and reported only.

**S3 two-sided maker (what the cited wallets actually do).**
- Every minute, rest 100-lot bids at the best YES bid and the best NO bid.
- Fills are taken from the trade tape, using both rules from the LIP test:
  "through" and "touch".
- Each fill is held to settlement. Pairs of YES + NO are complete sets that pay
  $1 regardless of the outcome; settlement P&L is identical either way.

## Pass bar (each strategy separately, confirmation half)
- Unit: one observation per market.
- Requirements: ≥ 300 trades, mean P&L > 0, day-clustered t ≥ 2, ROI ≥ 1% of cost.
- The direction must also be positive on the discovery half.
- For S3 the bar applies to the "through" rule.
- Number of tests: 3 strategies, with S1 at 2 entry minutes, so 4 primary cells.
  Expected false passes at t ≥ 2 one-sided ≈ 0.1.
