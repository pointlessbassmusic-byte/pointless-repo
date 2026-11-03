# Pre-registration: Kalshi daily AAA gas ladder vs yesterday's print (2026-10-26)

Written before any price in the series was scored. Up to this point the
data touched was the market list: ticker, times, strikes, settled value,
and the result of one event, KXAAAGASD-26OCT26, settled 4.2858.

## Question

Does Kalshi's KXAAAGASD ladder misprice a one-day change in a number its
traders already know? AAA's national average regular price is published
once a day. The ladder for date t trades from 12:00Z on t−1 until 03:59Z
on t, which is midnight ET. It closes before AAA publishes t's figure, so
every trader knows the t−1 value. The candidate reference is that print
plus the series' own recent drift. This is a "thin crowd against a public
number" test, the property CLAUDE.md names for the reference-price class.

## Data

- **Events.** Every settled KXAAAGASD event from 2026-03 on, the daily
  product with half-cent strikes. The 2023–24 AAAGASD events are a
  different product and are excluded.
- **Truth.** `V_t` is each market's `expiration_value`. `V_{t−1}` is the
  previous calendar day's event. An event with no previous-day event is
  skipped.
- **Decision.** The hourly candle ending closest to close − 2 h, within
  1 h of it. That is about 22:00 ET on t−1, long after AAA's t−1 figure is
  out.
- **Quotes.** Only rows with a two-sided quote (`yes_bid` < `yes_ask`) and
  an ask in [0.03, 0.97] count.

## Model (primary, fixed now)

- `Δ_s = V_s − V_{s−1}` for every prior day in the data.
- `μ_t` = mean of the 5 most recent Δ known at the decision. That is
  Δ_{t−1} and the four before it, all published by then.
- `σ_t` = sd of `Δ_s − μ_s` over the 60 most recent prior days, floored at
  $0.002. At least 20 prior residuals are required, or the event is skipped.
- `P(V_t > K) = 1 − Φ((K − V_{t−1} − μ_t) / σ_t)` for a "greater" strike.

The secondary model, zero drift (`μ = 0`), is reported but cannot pass
anything.

## Scoring

1. **Brier.** Model vs book mid on every row, as an event-clustered
   paired difference with a 95% CI.
2. **Taker rule.**
   - Buy YES at the ask when `p − ask ≥ 0.05`.
   - Buy NO at `1 − bid` when `(1 − p) − (1 − bid) ≥ 0.05`.
   - One contract each, net of Kalshi's marginal taker fee,
     0.07·P·(1−P) × the series multiplier.
   - Settled at the market's own result.

**PASS** requires all of:
- at least 100 taker trades;
- a mean net return per contract > 0, with an event-clustered t ≥ 2;
- the same sign in each chronological half;
- a model Brier no worse than the book's (CI upper bound ≤ +0.002).

Anything else is FAIL, or INCONCLUSIVE under 100 trades. A PASS buys a
recorder and a pre-live gate, not an executor.

No other thresholds, drift windows, decision times or model variants will
be scored against this verdict. Anything run afterwards is exploratory and
labelled as such.
