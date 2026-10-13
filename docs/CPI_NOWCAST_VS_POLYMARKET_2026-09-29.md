# Does the Cleveland Fed nowcast lead Polymarket's CPI buckets? No.

`python -m src.cpi_study` in polymarket-edge; rows in
`polymarket-edge/docs/cpi_rows.csv`. Rerunnable, no key needed.

## The question

The reference-price class has one confirmed member: fed-funds futures lead
Polymarket's Fed decision buckets by days. CPI is the nearest candidate in
the same class. Polymarket lists one-decimal buckets on each month's
headline and core CPI, year-over-year and month-over-month, with real volume
($0.1M-$3.3M per event). The Cleveland Fed publishes a daily nowcast of the
same four numbers, free, and its chart data files
(`nowcast_month.json`, `nowcast_year.json`) hold every daily vintage per
target month since July 2013 together with the released actual. That makes
the nowcast's own error distribution measurable out-of-sample and turns each
bucket into a probability with no free parameter.

## Method

- **Nowcast error.** For every target month before 2025, error = released
  actual minus the final vintage. Fitted on 137 months per series:

  | series | bias | sigma (pct-pts) |
  |---|---|---|
  | headline MoM | +0.010 | 0.151 |
  | headline YoY | +0.012 | 0.161 |
  | core MoM | +0.004 | 0.159 |
  | core YoY | +0.004 | 0.166 |

- **Bucket probability.** P(published value rounds into the bucket) under
  Normal(nowcast + bias, sigma), using the last vintage dated strictly before
  the horizon's calendar day (a vintage counts only from the day after its
  date). Edges are half-way rounding points; "or more" / "or less" buckets
  are open on one side. Every question template on the site is parsed,
  including the inverted tail on "decrease by N% or more".
- **Polymarket.** Every settled US CPI bucket under the `cpi` tag (UK,
  Canada, China, Argentina and other foreign releases excluded by title):
  37 events, 279 buckets, Feb 2025 to Sep 2026. Price = last trade at 24h
  and 1h before the 08:30 ET release (DST-aware). Outcome from resolution.
- **Cross-check.** For 36 of 37 events the winning bucket is the rounded
  Cleveland actual. The exception is November 2025 monthly, the release
  distorted by the autumn 2025 data gap the Cleveland Fed documents
  separately; it is kept, scored on Polymarket's resolution.

## Result: the market beats the nowcast, and the nowcast adds nothing

Paired Brier, bootstrap 95% CI resampling events:

| horizon | Brier Polymarket | Brier nowcast | diff | 95% CI |
|---|---|---|---|---|
| 24h | 0.0661 | 0.0844 | -0.0183 | [-0.0297, -0.0074] |
| 1h | 0.0555 | 0.0844 | -0.0289 | [-0.0421, -0.0158] |

Regression of the outcome on both probabilities: at 24h beta_polymarket
+1.02 (t 8.7), beta_nowcast +0.06 (t 0.3); at 1h +1.09 (t 12.0) and +0.02
(t 0.1). The price already contains whatever the nowcast knows.

By series at 1h: headline MoM -0.034 [-0.054, -0.012], headline YoY -0.029
[-0.052, -0.009], core YoY -0.035 [-0.080, -0.003], core MoM -0.003
[-0.052, +0.059] (n = 24). No series where the nowcast wins.

**Taking the nowcast's side loses.** Buy YES when the nowcast probability
exceeds the price by a threshold, buy NO when the price exceeds the nowcast,
1c spread: at 1h, n = 149 / 94 / 43 for thresholds 5 / 10 / 20 points,
returns -42% / -17% / -47% per $1, hit rates 20-26%. At 24h the mean
returns are positive at two thresholds with standard errors of 0.7: a
handful of longshot payoffs, not a result.

**Lead-lag.** The price move from 24h to 1h regressed on the 24h gap
(nowcast minus price) gives beta +0.16 (t 3.3). Some of that is a stale 24h
last-trade converging on a live consensus that happens to correlate with
the nowcast, the same one-way bias as in the venue study; the Brier and
regression results at 1h show the convergence leaves no residual
information in the nowcast.

**Fed-style footprint.** A bucket priced >= 0.90 a day out: 5 rows, 5 paid,
+2.8% per $1; at 1h, 7 rows, 7 paid, +2.4%. The same shape as the Fed rule
and far too few to mean anything (rule of three: surprise rate bounded at
~43-60%).

## What this changes

- The Cleveland Fed nowcast is not a leading reference for CPI buckets. A
  reference has to lead the market; a public model the market already reads
  does not, and the crowd on these markets also has the consensus forecasts
  and the other nowcasts.
- The reference-price class now stands at one confirmed member and four
  measured non-members: sportsbook consensus, the other venue, in-house Elo,
  the CPI nowcast. What distinguishes the Fed case is that the reference is
  itself a deep market pricing the identical event, not a model or a
  forecaster. The next candidate should have that property or be measured
  first the same way.
- The >= 0.90 favourite footprint on CPI is worth recording forward, not
  trading: 12 rows in total.

## Caveats

- Polymarket's price is the last trade; the CPI events trade less than the
  soccer markets and a stale print biases the 24h rows toward "the price
  later moved". The 1h rows are the cleaner comparison and they are the more
  negative for the nowcast.
- Polymarket CPI markets are not sports markets; no taker fee is charged
  here. Adding one would only lower the strategy rows further.
- The normal error model is the simplest choice; the empirical error
  distribution over 137 months is close to normal at these sigmas and the
  Brier gap is far larger than a shape correction could close.
