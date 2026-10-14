# Pre-registration: the house side of sports combos on Kalshi (2026-10-14)

Committed before any settled combo outcome was read.

## Why

The operator's own Polymarket US record is 124 combo/single bets, $3,551
staked, −19.7%. Table-tennis combos alone lost −37.6% and tennis combos
−8.5%. The app has no way to take the other side of a combo; its quoters
do. Kalshi lists every combo as an ordinary binary market (multivariate
events, series `KXMVESPORTSMULTIGAMEEXTENDED`), settles it publicly, and
lets API members answer combo RFQs. So "the opposite of what I did" can
be measured: the NO side of sports combos at the price combo buyers paid.

## Data

Every settled market in `KXMVESPORTSMULTIGAMEEXTENDED` that the public API
returns (paginated to the end or 20,000 markets), with volume > 0 and a
last price in [0.01, 0.99]. The last price is the most recent fill: what a
YES buyer paid and what the NO side received.

## Measure

Per contract, the NO side at the last price returns `(p − y) / (1 − p)`
per $ staked, where y = 1 if YES settled. The Kalshi taker fee
0.07·p(1−p) is subtracted (a quoter may pay less; this is conservative).

- Weights: per market (every combo counts once).
- Clusters: the close date (UTC). Combos on one slate share legs.
- Split by close time: the first 70% of dates is discovery, the last
  30% confirmation.

## Decision

- **PASS:** the confirmation-half mean net NO return per $ is above 0,
  its date-clustered bootstrap 95% lower bound (2,000 draws, seed 5) is
  above 0, there are at least 300 markets, and the discovery half has
  the same sign.
- **FAIL:** anything else.

Also reported, descriptive only: price buckets, leg counts, and the
volume-weighted version.

## What a PASS buys

Being the house on combos means answering Kalshi's combo RFQs as a quoter
(API member), not buying NO on the screen. A PASS justifies building a
paper RFQ responder, then a minimum-size live one behind the pre-live
gate. It does not justify anything in the app.
