# Does the strategy make money? MLB and tennis, measured on real Kalshi games

_2026-09-23. Reproduce with `sportsbot market-backtest`._

**No. The MLB model has no measurable edge against Kalshi's price and barely
trades; the bootstrapped tennis model has a *negative* edge and loses about
18% per bet. Do not put real money on either.**

## Method

`backtest/engine.py` scores the model against outcomes — whether it is
calibrated. That is not the profitability question: a well-calibrated model
still loses to a better-calibrated price. `backtest/kalshi_market.py` scores
it against the price it would actually have paid.

- **910 settled Kalshi MLB games**, 2026-07-16 → 2026-09-22.
- **Walk-forward ratings.** Every game is predicted with a model built only
  from games that finished earlier, then used to update it — the live loop's
  exact order. Predictions go through the real `BaseballModel.predict`, not a
  reimplementation, so the backtest cannot drift from the scanner.
- **Real recorded quotes** from Kalshi's hourly candlesticks at a chosen lead
  before **first pitch**, not a mid reconstructed afterwards.
- **Real fees** from the same `kalshi_taker_fee` the executor charges, at
  MLB's true 0.5 multiplier.

One methodological trap worth naming, because it inverts the answer: these
markets stay open **through the game** and settle hours after it ends. The
last quote before market close is a near-settlement price that already knows
the result, so measuring CLV against it just asks whether the bet won. Both
the entry and the closing line are therefore anchored to first pitch (parsed
from the event ticker, which is stamped in US Eastern), and the candle
straddling first pitch is discarded.

## Result 1: at the production threshold it barely trades, and the bets go nowhere

> **Correction (2026-09-23).** The first version of this table was computed
> with `kalshi_taker_fee` at its default multiplier of 1.0 — the same bug this
> harness was written to help find, reproduced inside the harness. MLB runs
> 0.5, so every bet was priced against twice the fee the executor actually
> pays, which suppressed bets and made the strategy look worse than it is.
> The numbers below are the corrected run. The conclusion did not change; some
> of the cells did, and the earlier ones should not be cited.

| lead | min edge | bets from 900 games |
|---|---|---|
| 24h | 0.03 | 2 |
| 6h | 0.03 | 2 |
| 2h | 0.03 | 3 |

Two or three bets from 900 games is not a strategy. Lowering the bar finds
volume but no edge — and the sign flips with the lead:

| lead | bar | bets | ROI | hit | mean CLV | CLV+ |
|---|---|---|---|---|---|---|
| 24h | 0.005 | 37 | +0.096 | 0.513 | +0.0097 | 0.703 |
| 24h | 0.010 | 18 | −0.012 | 0.389 | +0.0058 | 0.667 |
| 6h | 0.005 | 54 | −0.033 | 0.426 | −0.0025 | 0.333 |
| 6h | 0.010 | 38 | −0.092 | 0.368 | −0.0041 | 0.316 |
| 2h | 0.005 | 57 | −0.084 | 0.404 | −0.0040 | 0.210 |
| 2h | 0.010 | 41 | −0.110 | 0.366 | −0.0044 | 0.195 |

The cells with real sample size (38–57 bets, at 6h and 2h) are consistently
**negative**, in ROI and in CLV alike. The one attractive cell — +9.6% at 24h
on the loosest bar — has n = 37, so its standard error on ROI is about 16
percentage points: t ≈ 0.6, indistinguishable from zero.

The 24h rows do show positive CLV (+0.010, 70% positive), but that is most
likely the market's own drift rather than skill: prices drift +0.0059 toward
the home side from 24h to close (Result 3), so any home-leaning book collects
it without forecasting anything.

## Result 2: the model carries no information the price lacks

Threshold-free, 873 games with both a model prediction and a pre-game quote:

| arm | Brier | log loss |
|---|---|---|
| market mid | **0.2399** | **0.6723** |
| model alone | 0.2422 | 0.6773 |
| blend, 0.30 model (production) | 0.2400 | 0.6727 |
| blend, 0.15 model | 0.2399 | 0.6724 |
| always home (base rate 0.5452) | 0.2480 | 0.6890 |

The market beats the model outright, and blending the model in makes the
forecast *worse* than the market alone — the blend weight's only effect is to
decide how much damage it does.

The decisive number is the regression of the market's error on the model's
disagreement:

```
corr(model − market, outcome − market) = +0.0067   (n = 873)
regression beta                        = +0.065    t = 0.20
```

A model with real edge would have beta near 1: when it says the price is two
points too low, the price would be about two points too low. At 0.065 the
disagreement is ~93% noise, and t = 0.20 cannot distinguish it from zero.
**No threshold, blend weight, or stake rule recovers an edge that is not
there.**

## Result 3: no model-free drift to trade either

Prices do drift toward the home side as the game approaches — small but
statistically real, and it shrinks monotonically as the spread tightens:

| lead | mean (close − mid) | t | mean spread |
|---|---|---|---|
| 24h | +0.0059 | +5.41 | 0.0117 |
| 6h | +0.0027 | +3.56 | 0.0103 |
| 2h | +0.0009 | +2.86 | 0.0100 |

It is not worth trading. Taking the home side on every game at 24h returns
+0.45% (ROI +0.0045 over 873 games) — inside the noise and gone at the first
slippage. The away side returns −6.7%, which is the spread and fee drag
showing up as it should. Crossing a ~1.1-point spread and paying ~0.9 points
of fee to capture a 0.6-point drift does not work.

## Result 4: the maker variant is not measurably better

The spread averages **0.0103 — one tick**. "Maker-first inside the spread",
which the strategy config assumes, is therefore impossible here: there is no
room between bid and ask, so the only maker option is joining the best bid and
waiting for someone to sell into you.

Simulated against real candles (a resting buy at P fills if a later pre-game
candle traded at or below P on non-zero volume), buying the home side and
holding to settlement, paying the maker fee (25% of taker on
`quadratic_with_maker_fees`, so 0.125x the base rate on MLB):

| lead | fill model | filled | hit | ROI | 95% CI | t |
|---|---|---|---|---|---|---|
| 24h | optimistic (low ≤ P) | 691/780 (89%) | 0.537 | +0.0191 | [−0.054, +0.092] | +0.51 |
| 24h | strict (low < P) | 433/780 (56%) | 0.522 | −0.0067 | [−0.099, +0.086] | −0.14 |
| 6h | optimistic | 750/780 (96%) | 0.540 | +0.0138 | [−0.056, +0.083] | +0.39 |
| 6h | strict | 243/780 (31%) | 0.572 | +0.0917 | [−0.032, +0.215] | +1.46 |

**Every confidence interval spans zero.** The best-looking cell (+9.2% at 6h
strict) is also the one with the heaviest selection effect — it only fills
when the market trades down through the resting bid, which is adverse
selection by construction — and it still cannot clear t = 2.

The strict model is the honest bound for a retail account: at a one-tick
spread you sit at the back of the queue, so you are filled mainly when the
price is moving against you.

## Result 5: tennis — the bootstrap model is worse than a coin, and its bets lose

Same method on Kalshi tennis (`sportsbot market-backtest tennis`): 1,577
settled ATP/WTA matches, 2026-07-18 → 2026-09-23, ratings bootstrapped from
the same settled markets and walked forward day by day. Pre-match anchor is
settlement − 3h (tickers carry no start time and `occurrence_datetime` is a
slot a quarter of matches finish before).

Every cell of the threshold sweep is negative, and not marginally:

| lead | bar | bets | ROI | hit | mean CLV | CLV+ |
|---|---|---|---|---|---|---|
| 12h | 0.03 | 110 | −0.205 | 0.291 | −0.0055 | 0.382 |
| 6h | 0.03 | 105 | −0.236 | 0.276 | −0.0045 | 0.248 |
| 2h | 0.03 | 109 | −0.204 | 0.284 | −0.0074 | 0.193 |
| 6h | 0.005 | 168 | −0.178 | 0.316 | −0.0105 | 0.250 |

At n ≈ 150 the standard error on ROI is about 8 points, so −18% is roughly
t = −2.2. This is not "no edge"; it is a **negative** edge — the bets the
model selects lose at a rate well beyond fees.

The threshold-free test says why (n = 1,561; the 307 rows the live scanner
would actually price, at uncertainty ≤ 0.20, in parentheses):

| arm | Brier |
|---|---|
| market mid | **0.2024** (0.1891) |
| blend 0.30 | 0.2081 (0.1960) |
| base rate | 0.2498 (0.2455) |
| model alone | **0.2589** (0.2422) |

The model scores *worse than the base rate* — worse than always picking the
side that wins 51% of the time. And the regression of the market's error on
the model's disagreement is **negative**: beta −0.027 over everything, −0.119
on the priceable subset. When this model disagrees with the price, the price
is right more often than not. The blend does not rescue it: 0.30 model weight
makes the forecast worse than the market alone by 0.006.

Maker on the priced side, held to settlement, at the tennis maker fee (0.25×
base): −2.1%, −1.9%, −2.7%, −6.9% across lead × fill model, every interval
spanning zero. Drift is −0.0001 (t −0.09): nothing to capture.

The mechanism is not mysterious. Two months of match results with no surface,
no head-to-head, no form, no injury news, on a tour where a third of the
field turns over — the bootstrap Elo is the *least* informed participant in
that market. Its "disagreements" are mostly the things it does not know.
That is exactly the adverse selection a thin model should expect, and the
allocator now treats provisional ratings as untradeable rather than halved.

**Sackmann ratings are untested here and would be a different artifact** —
decades of history with surfaces. Nothing in this section rules them out.
Nothing supports them either until the same measurement is run on them.

## Result 6: no favorite–longshot bias to trade either

The best-documented inefficiency in betting markets is model-free: longshots
overpriced, favorites underpriced. Tested on every cached market, bucketed by
the pre-match closing mid, both sides of each market counted (2,477 markets,
4,682 side-observations), taker entry at the 6h ask with real fees.

Tennis, the large sample, is calibrated to within three points everywhere:

| closing mid | n | implied | actual | taker ROI (±95%) |
|---|---|---|---|---|
| 0.05–0.15 | 155 | 0.104 | 0.103 | −0.263 ± 0.411 |
| 0.15–0.25 | 287 | 0.204 | 0.213 | −0.106 ± 0.220 |
| 0.25–0.35 | 387 | 0.303 | 0.292 | −0.118 ± 0.150 |
| 0.35–0.45 | 525 | 0.400 | 0.419 | −0.008 ± 0.105 |
| 0.45–0.55 | 369 | 0.500 | 0.501 | −0.052 ± 0.101 |
| 0.55–0.65 | 526 | 0.599 | 0.580 | −0.062 ± 0.071 |
| 0.65–0.75 | 386 | 0.696 | 0.705 | −0.008 ± 0.067 |
| 0.75–0.85 | 289 | 0.795 | 0.789 | −0.018 ± 0.060 |
| 0.85–0.95 | 156 | 0.895 | 0.897 | +0.009 ± 0.056 |

Pooled favorites (mid ≥ 0.65, n = 852): implied 0.773, actual 0.776, taker
ROI **−0.4% [−4.2%, +3.5%]**, t −0.19. Pooled longshots (n = 850): −16.3%,
t −2.53 — significant, and not a bias: it is the fee and spread as a share of
a small stake (at p = 0.20 the taker fee alone is 5.6% of stake). You cannot
earn that; you can only stop paying it.

MLB has almost all its mass in 0.35–0.65 (the games are close). Its favorite
bucket looks better — 0.65–0.75, n = 78, implied 0.680, actual 0.731 — and
pooled favorites (n = 83) return +8.3%, but the interval is [−5.6%, +22.2%],
t 1.17. At that sample size a +5-point calibration gap is inside one standard
error of the win rate. It is the kind of cell that would be cherry-picked and
should not be: with nine buckets across two sports, one at t ≈ 1.2 is
expected by chance.

## Why a few hundred bets can never settle this

Per-bet return standard deviation on ~50c binaries is about 1.0. That fixes
how much data any PnL claim needs:

| edge to detect | bets needed (t = 2) |
|---|---|
| 5% ROI | 1,600 |
| 2% ROI | 10,000 (~4 MLB seasons) |
| 1% ROI | 40,000 (~16 seasons) |

So **any ROI claim from a few hundred sports bets is noise**, including the
encouraging-looking ones above. This is not a reason to gather more PnL; it
is a reason to stop using PnL as the decision metric.

Closing-line value is the metric that fits the data we can actually get. Its
per-observation standard deviation is 0.0222 at a 6h lead against ~1.0 for
returns, so:

| CLV to detect | observations needed (t = 2) |
|---|---|
| +0.005 (half a point) | 79 |
| +0.002 | 493 |

We have 873. CLV is measurable here, and it says there is no edge — which is
exactly why the go-live gate in `deploy/DEPLOYMENT.md` is CLV-based rather
than PnL-based. That choice is now backed by a power calculation rather than
a principle.

## What this rules in and out

- **Out: taker betting MLB moneylines on this model.** Three independent
  measurements agree, and the strongest of them (beta = 0.065) says the
  signal is absent rather than small.
- **Out: threshold tuning.** The edge bar is not what is stopping it.
- **Out: favorite–longshot bias.** Tennis is calibrated within three points
  in every bucket on 3,122 observations; MLB's one attractive bucket is
  n = 83 at t 1.2.
- **Out: maker capture of the drift, on the evidence available.** Every
  variant's confidence interval spans zero, and the one-tick spread means
  there is nowhere to rest except the back of the touch queue, where fills
  arrive mainly when the price is moving against you.
- **Not answerable by simulation:** whether a real resting order fills better
  than the strict model assumes. Queue position is the one thing candles
  cannot show. If anything here deserves capital, it is a minimum-size live
  maker order purely to record fill behaviour — a data-collection exercise
  with a known cost, not a strategy.

The go-live gate stands: the paper book has produced no settled bets, so
none of the four criteria is met, and this backtest is a reason to expect
that to continue rather than a reason to relax them.
