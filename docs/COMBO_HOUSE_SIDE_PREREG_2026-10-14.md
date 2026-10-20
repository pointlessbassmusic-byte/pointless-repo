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

## Result (2026-10-14, read once): FAIL — Kalshi sports combos are priced fairly

`KXMVESPORTSMULTIGAMEEXTENDED`, settled. The public API's settled listing
returned 20,000 markets (the pre-registered cap) covering only 10 close
dates, 2026-08-17 → 08-30. Of these, 15,224 had volume and a last price
in [0.01, 0.99].

| half | markets | dates | NO side net per $ (95% CI, date-clustered) | YES settle rate | mean price |
|---|---|---|---|---|---|
| discovery | 15,208 | 7 | −0.037 [−0.170, +0.042] | 0.200 | 0.194 |
| confirmation | 16 | 3 | −0.039 [−0.583, +0.417] | 0.312 | 0.335 |
| all | 15,224 | 10 | −0.037 [−0.182, +0.043] | 0.201 | 0.195 |

The verdict is **FAIL** on every count:

- **The sign is wrong.** The NO side loses in both halves.
- **The confirmation half is far too small.** It holds 16 markets against a bar of 300, because the listing's dates were almost all in the discovery 70%.

The substance does not depend on that split. Combo YES settled at 20.1%
against an average fill price of 19.5¢: buyers are paid fairly, if
anything slightly more than fairly. Gross of fees the house side is about
zero in every price bucket below 0.50, and negative above it. Above 0.70,
YES settled at 0.865 against a price of 0.849.

The volume-weighted NO net is −0.024 per $. Across leg counts only the
14–22-leg tail is positive, on n ≤ 223 per cell, which is not a test.

**What it means.** On Kalshi, being the house on sports combos earns
nothing at the prices combo buyers pay; the quoters' edge, if any, is
smaller than one Kalshi taker fee. The operator's −19.7% on Polymarket US
combos is therefore not the mirror image of a harvestable house edge. Most
of it is table-tennis combos (−37.6% on 70 bets, about t −2), plus parlay
variance (tennis −8.5% on 49 bets is well inside the noise). Do not build
an RFQ responder on this evidence.
