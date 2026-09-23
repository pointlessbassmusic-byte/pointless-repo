# Does the MLB strategy make money? Measured on 910 real Kalshi games

_2026-09-23. Reproduce with `sportsbot market-backtest`._

**No. The model has no measurable edge against Kalshi's price, and the
current strategy places almost no bets. Do not put real money on it.**

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

## Result 1: at the production threshold, it never trades

| lead | min edge | bets from 910 games |
|---|---|---|
| 24h | 0.03 | **0** |
| 6h | 0.03 | **0** |
| 2h | 0.03 | 1 (lost) |

Lowering the bar does not find a hidden edge, it just finds noise:

| lead | bar | bets | ROI | hit | mean CLV |
|---|---|---|---|---|---|
| 24h | 0.005 | 8 | +0.328 | 0.500 | +0.0106 |
| 24h | 0.010 | 3 | +0.811 | 0.667 | +0.0067 |
| 6h | 0.005 | 15 | +0.022 | 0.400 | −0.0040 |
| 6h | 0.010 | 5 | +0.107 | 0.400 | −0.0040 |
| 2h | 0.005 | 18 | −0.045 | 0.389 | −0.0036 |
| 2h | 0.010 | 9 | +0.062 | 0.444 | −0.0044 |

Eight to eighteen bets out of 910 games, with ROIs swinging from −4% to +81%
on three-bet samples. Those are not results, they are coin flips.

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
