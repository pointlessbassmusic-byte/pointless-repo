# Result 15 on Kalshi's own tape — result (2026-10-14)

Pre-registered in `docs/KALSHI_TOUCH_PREREG_2026-10-14.md` (amendment
1: 6 h window, at most 5,000 prints per market). Read once.

## Data

2,116 settled markets from the last 30 days and 1.6 M pre-game prints
(103 markets hit the 5,000-print cap):

| sport | markets | games |
|---|---|---|
| MLB | 426 | 213 |
| NHL | 328 | 164 |
| NFL | 126 | 63 |
| NCAAF | 918 | 459 |
| soccer (EPL, UCL, La Liga, MLS) | 318 | 106 |

Pre-game cut: the MLB ticker's first pitch; everywhere else
occurrence − 4 h.

## Population touch-maker, test half (last 30% of games by start)

| sport | deadline | test games with fills | CLV per fill (95%, game-clustered) | realised return per $ | verdict (120 s) |
|---|---|---|---|---|---|
| **NHL** | 120 s | 41 | **+0.67¢ [+0.61, +0.71]** | +0.020 [−0.066, +0.096] | **PASS** |
| | 30 min | 42 | +0.92¢ [+0.84, +1.00] | +0.030 [−0.079, +0.132] | |
| NFL | 120 s | 16 | **+0.94¢ [+0.87, +1.01]** | +0.207 [−0.175, +0.578] | INCONCLUSIVE (16 < 30 games) |
| MLB | 120 s | 69 | **+0.65¢ [+0.23, +0.95]** | −0.053 [−0.128, +0.028] | INCONCLUSIVE (return < 0) |
| | to cut | 69 | +0.64¢ [+0.16, +0.95] | −0.041 [−0.108, +0.031] | |
| soccer | 120 s | 35 | **+1.12¢ [+0.74, +1.50]** | −0.037 [−0.292, +0.189] | INCONCLUSIVE (return < 0) |
| NCAAF | 120 s | 93 | +0.33¢ [−0.72, +0.91] | +0.009 [−0.260, +0.303] | INCONCLUSIVE (CLV spans 0) |

## Reading

**The touch edge replicates on the venue the live test runs on.** Resting
one tick behind pre-game taker prints beats the pre-game close by about
two-thirds of a cent to a cent per fill on MLB, NHL, NFL and soccer, with
every interval above zero. That is Result 15's size on the international
Polymarket (+0.5 to +0.6 MLB, NHL and NFL; +0.7 to +1.4 soccer), now on
Kalshi's book, after Kalshi's maker fee.

Realised P&L at 16–69 test games is noise around zero:

- **Positive:** NHL and NFL.
- **Slightly negative:** MLB and soccer.
- **Every return interval spans zero.**

This is the sample-size statement Result 15 made, not a contradiction;
compare its esports rows, where the return interval sat wholly below
zero.

## What changes, per the pre-registration

- **NHL** passes. It stays in the live test's Kalshi touch arms.
- **MLB and NFL** are inconclusive, not failed. They stay: their CLV
  intervals are above zero, and nothing here argues for removing them.
- **NCAAF and soccer** are not added to the Kalshi live arms. Soccer's
  CLV is the strongest of all, so it is the first candidate once its
  realised return is readable. The paper sim already quotes it on
  Polymarket.

## The caveat, unchanged

Every fill here is credited as if the order were first in the queue at
its level. A new $1 order on Kalshi sits behind the existing size. That
fill share is the one number only the live test can measure, which is
why it runs at $1.

## Amendment 2 read: the favourite-side rule is NOT CONFIRMED (dropped)

The rule, chosen on the train half: rest only when the fill price is
≥ 0.50. Confirmation on the test half, MLB, NHL and NFL pooled, 120 s:

| | fills | games | CLV per fill | realised return per $ |
|---|---|---|---|---|
| all fills | 340,011 | 126 | +0.73¢ [+0.39, +0.94] | +0.015 [−0.100, +0.141] |
| price ≥ 0.50 | 170,438 | 126 | +0.01¢ [−2.12, +1.29] | −0.100 [−0.351, +0.147] |

The train-half pattern was noise: out of sample the favourite side is the
worse half, not the better one. The touch arm keeps resting on both
sides, as the live config already does. No refinement is adopted from
this data.

## Correction (2026-10-15): this is the front-of-queue upper bound; queue-adjusted, NHL goes

The construction above credits a fill whenever a later print trades at
or through the order's level. That is the at-or-through rule, which
assumes the order was **first** in the queue. Main's Result 15 addendum
3 (#82) measured the queue directly on 16 NHL games with Kalshi's book
depth. Its front-of-queue Kalshi number, +0.66¢ per fill, matches this
document's +0.67¢. Counting the queue in front changes the picture:

| | joins | fill rate | CLV per fill |
|---|---|---|---|
| Kalshi: join the existing queue | 1,214 | 22% | −0.24 ± 0.31¢ |
| Kalshi: first at each new best price | 41 | 68% | +0.07 ± 0.12¢ |

This dataset has no book depth, so it cannot run that check. The
pre-registered NHL PASS above therefore stands **as recorded, for the
upper bound only**. It is not evidence of a fundable edge.

The same caution applies to MLB, NFL and soccer's positive CLV: every
row here is the same front-of-queue bound. Real fills lie between that
bound and the queue-adjusted numbers, and only the $1 live test
measures where. Per the ledger's rule the PASS row is not edited; a
correction row is added.
