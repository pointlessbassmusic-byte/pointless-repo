# Does the strategy make money? MLB and tennis, measured on real Kalshi games

> **Correction, 2026-09-28.** Every result below was measured on **Kalshi**.
> The repo's architecture puts sports on **Polymarket** (Kalshi is the
> substrate venue), and the reason the sim was moved to Kalshi on 2026-09-22
> — "Polymarket has nothing tradeable for these sports" — was **wrong for
> tennis and MLB**. It came from a discovery bug: `list_sports_markets`
> ordered Gamma events by `startDate`, so a game day's dozens of
> inning-winner derivatives filled the page ahead of the games, the
> moneyline filter then discarded them, and the slate looked empty. Measured
> on the markets Polymarket actually trades (2026-09-28): tennis moneylines
> median spread **0.010**, 22 of 25 ≤ 0.03, $2.25M 24h volume across 100
> events; MLB moneylines median spread **0.010**, 9 of 9 ≤ 0.03. As tight as
> Kalshi. The table-tennis finding stands: Setka Cup on Polymarket does $34
> a day across 100 events.
>
> So the Kalshi results are Kalshi results. They say nothing about
> Polymarket. The Polymarket measurements are Results 8 (MLB) and 9
> (tennis) below, built on `backtest/polymarket_market.py`. Fees, from the documentation's "Sports
> Market Fees" page rather than Gamma's raw fields: **taker only**,
> fee = C × 0.05 × p × (1 − p), so 1.25 points at p = 0.5 (100 shares at
> $0.50 → $1.25); **makers pay nothing and receive a 15% rebate.** That is
> comparable to Kalshi (0.875 MLB / 1.75 tennis), not six times worse as an
> earlier draft of this note said — and the repo's `taker_fee` had charged
> 0.10 × min(p, 1−p), four times the documented fee at the middle. Fixed.

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

## Result 1 (Kalshi): at the production threshold it barely trades, and the bets go nowhere

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

## Result 2 (Kalshi): the model carries no information the price lacks

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

## Result 3 (Kalshi): no model-free drift to trade either

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

## Result 4 (Kalshi): the maker variant is not measurably better

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

## Result 5 (Kalshi): tennis — the bootstrap model is worse than a coin, and its bets lose

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

## Result 6 (Kalshi): no favorite–longshot bias to trade either

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

## Result 7: the weather market already contains the forecast

The NWS arm is the strongest signal in the stack — it beats climatology on
4 of 5 dates (`docs/SIGNALS_2026-09-17.md`) — and it loses to the market on
Brier (0.1408 vs 0.0802). The question that matters for trading is not
whether it loses but whether it carries *anything the price has not
absorbed*. Same threshold-free regression as Results 2 and 5, on 168 settled
strike-band rows across 28 station-days, with significance clustered by
station-day because the six bands of a day share one temperature:

```
beta of (outcome − market) on (NWS − market) = +0.036
cluster-bootstrap 95% CI [−0.106, +0.222]      t = 0.43
control, climatology in place of NWS          = −0.004
```

| arm | Brier |
|---|---|
| market | 0.0802 |
| market + β·(NWS − market) | 0.0801 |
| 0.9 market + 0.1 NWS | 0.0804 |
| 0.8 market + 0.2 NWS | 0.0819 |
| NWS | 0.1408 |

Adding the forecast to the price is worth 0.0001 of Brier. Any fixed blend
is worse than the price alone. **The market already contains the forecast.**

The mechanism is visible by price bucket. Where the market prices a band at
0.012 (87 bands), NWS says 0.147 and the actual rate is 0.000; where the
market says 0.995, NWS says 0.300 and the actual is 1.000. The forecast arm
points the right way with far too wide a sigma: it beats climatology on
direction and loses to a market that is already well calibrated in exactly
the places NWS is not. Tightening sigma on these 28 station-days would be
fitting the test set, and is not done.

So the weather arm's standing is now exact: a genuine, measured
*conventional baseline* for the substrate, and not a source of bets. That is
the role the protocol always assigned it.

## Result 8 (Polymarket): MLB — same answer as Kalshi, measured to a third of a point

_2026-09-29. Data: every resolved Polymarket MLB moneyline whose CLOB price
history still exists. The CLOB keeps hourly trade prices for about 30 days
(empty for games before 2026-08-28, populated from then on), so the sample is
2026-08-28 → 2026-09-27: 418 games, 416 with a history, 392–408 priceable
at the lead. Fee charged: the documented 0.05 × p × (1 − p) per share, taker
only. Decision price = last hourly print at the lead before first pitch
(`gameStartTime`), one-tick spread applied; closing line = last print
strictly before first pitch. Same BaseballModel, same walk-forward
(7,386 MLB Stats games, ratings only from earlier games)._

**Convention found and fixed:** Polymarket lists the **visitor** first on
MLB moneylines — 981 of 981 resolved games matched MLB Stats with
`outcomes[0]` = away (slug `mlb-{away}-{home}-{date}`). The client set no
`home_field`, so the live scanner was modelling `outcomes[0]` as the home
side and giving the home advantage (24 Elo points plus the starting-pitcher
overlay) to the wrong team on every Polymarket MLB game. Fixed in
`exchanges/polymarket.py` (`meta["home_field"] = outcomes[1]` for
baseball), with a test. The backtest oriented every game by MLB Stats'
real home team, so the numbers below are unaffected.

**Threshold-free (model vs the price it would pay, same games, same time):**

| lead | n | Brier model | Brier mid | Brier close | blend 0.30 | beta | t |
|---|---|---|---|---|---|---|---|
| 24h | 392 | 0.2403 | **0.2344** | 0.2343 | 0.2357 | −0.95 | −1.76 |
| 6h | 404 | 0.2403 | **0.2343** | 0.2347 | 0.2356 | −0.67 | −1.40 |
| 2h | 408 | 0.2400 | **0.2344** | 0.2347 | 0.2355 | −0.62 | −1.28 |

The mid beats the model at every lead; blending the model in makes the mid
worse; and the slope of (outcome − mid) on (model − mid) is negative at
every lead — when the model disagrees with Polymarket's price, the price is
the one that is right, if anything (none of the three slopes is
significant, but every one has the wrong sign). This is the Kalshi Result 2
(beta +0.065) again, on the other venue, on a fresh month of games.

**Model-free drift (close − decision mid, on the visitor's contract):**

| lead | mean | sd | t | n |
|---|---|---|---|---|
| 24h | −0.0047 | 0.0328 | −2.82 | 392 |
| 6h | −0.0022 | 0.0154 | −2.82 | 404 |
| 2h | −0.0008 | 0.0067 | −2.51 | 408 |

The home side gains about half a point over the last day before first
pitch. It is real (t −2.8), and it is the same direction as Kalshi's Result
3 (+0.0059 on the home ticker). It is also untradeable as a taker: the fee
at p = 0.5 is 1.25 points a side, 2.5 for the round trip, against 0.47 of
drift. As a maker the fee is zero and the one-tick spread would add a point
— **if** both legs fill. Kalshi's Result 4 measured adverse selection
taking the whole spread; that has not been measured on Polymarket and
cannot be from price histories. It needs fills (the minimum-size live
maker order that is still on the user's side).

**Threshold sweep (for completeness; nothing here is citable):** taker cells
place 0–17 bets in the month; maker cells place 83–98 and lose 3–22% of
stake with "positive CLV" in 74–86% of bets — the CLV there is the
bid-versus-mid entry the maker assumption grants, not information.

**Favorite–longshot:** mid 0.2–0.3 won 0 of 11; 0.3–0.4 won 21 of 63
(0.333 vs 0.362 mid, t −0.5); 0.6–0.7 won 24 of 37 (0.649 vs 0.633). Same
sign as the classic bias, nowhere near significance. Nothing to trade.

**Precision:** CLV at 24h has sd 0.033, so 392 games pin the model's mean
CLV to ±0.0017 (one se). A strategy that cleared the 1.25-point taker fee
would show up here. It does not. ROI, by contrast, has se ≈ 5% on 400
games and is not informative either way; judge by CLV.

Reproduce: `sportsbot market-backtest baseball --exchange polymarket`
(needs the CLOB histories; `data/cache/polymarket_prices/`).

## Result 9 (Polymarket): tennis — the model loses to the price; the one "bias" is a look-ahead

_2026-09-29. Data: every resolved Polymarket tennis moneyline with a CLOB
price history, 2026-09-01 → 09-28: 7,132 matches (ITF 3,476 priced, ATP
1,416, WTA 680; median market volume $1.6k, a quarter under $20),
4,864–5,881 priceable at the lead. Anchor = `gameStartTime` (see the
correction below). Fee = the documented 0.05 × p × (1 − p), taker only.
Model = the production TennisModel bootstrapped from settled matches
(1,577 Kalshi + 7,001 Polymarket, deduplicated), day-batched walk-forward,
the live scanner's uncertainty gate (≤ 0.20) applied for the "priceable"
rows._

**Correction first.** The first tennis sweep anchored on `closedTime − 3h`,
the Kalshi tennis anchor. On Polymarket `closedTime` trails `gameStartTime`
by a median **14 hours** (p10 3.7h, p90 20.7h; 300 markets) — resolution
lag, not match length — so that anchor sat after most matches and scored
in-play and post-result prints as the closing line (closes of 1.000 from
entries of 0.045; "+18% ROI"). That sweep is discarded. Everything below
uses `gameStartTime`, skips the 6% of markets whose start is not before
their close, and refuses a closing print at the rail (outside 0.03–0.97).
MLB Result 8 re-run under the same guard: identical.

**Model vs price, priceable rows (what the scanner would trade):**

| lead | n | Brier model | Brier mid | blend 0.30 | beta | t | taker CLV at bar 0.03 | n bets |
|---|---|---|---|---|---|---|---|---|
| 12h | 599 | 0.2453 | **0.2047** | 0.2064 | +0.10 | +1.2 | +0.0059 | 257 |
| 6h | 619 | 0.2440 | **0.2029** | 0.2041 | +0.12 | +1.5 | −0.0018 | 275 |
| 2h | 622 | 0.2438 | **0.2018** | 0.2032 | +0.11 | +1.4 | −0.0028 | 273 |

Kalshi Result 5 again: the bootstrapped model is far worse than the price
(four Brier points), blending it in makes the mid worse, the slope on
model disagreement is a tenth with t ≤ 1.5, and the bets it would place
have negative CLV at 6h and 2h (positive-CLV rate 20–27%: they pay the
spread and get nothing back). Across all 5,590 rows at 6h the slope is
+0.002 (t 0.07). Taker ROI in the sweep runs +4% to +17% with hit rates of
0.31–0.37 and per-bet return se of 0.11–0.14 — t ≈ 1, and CLV says no.
Maker cells (+11% to +23%, fills assumed) inherit the bid-versus-mid half
tick by construction and are not evidence.

**Model-free drift:** close − decision mid on priceable rows +0.0028
(t 1.0) at 12h, +0.0012 (t 1.0) at 6h, −0.0002 (t −0.3) at 2h. Nothing.

**The favorite–longshot bias that isn't.** Binned by decision mid, the
0.7–0.8 bin wins 0.79–0.80 against a mean mid of 0.747 at every lead
(t +2.6 to +3.7), and a model-free "buy the favourite ≥ 0.70" looked like
+6% ROI after fees in markets under $10k (t 4–7). Two things kill it:

1. *Look-ahead.* "Under $10k" is total volume at resolution. A favourite
   that cruises draws no in-play trading; an upset draws a lot. Bucketing
   by final volume selects on the result: in the 0.7–0.8 bin the win rate
   runs **0.917 → 0.793 → 0.746 → 0.683** across final-volume buckets
   (< $1k, ≥ $1k, ≥ $10k, ≥ $100k). Bucketed by a decision-time variable —
   trade prints before the decision — the gap is flat (+0.05, +0.06,
   +0.05, +0.04) and the strategy is **0 to +1% with t < 1** in every
   bucket (n 133–1,027). By tour, T ≥ 0.70 with the median live spread:
   ITF +0.4% ± 1.2, ATP +1.0% ± 2.2, WTA +1.0% ± 3.0.
2. *Spreads.* The backtest assumed one tick. Live books (483 open tennis
   moneylines, 2026-09-29 00:40Z): markets under $10k are **3 points wide
   at the median, 7 at p75, 29 at p90**; ITF 4 / 10 / 43. WTA 1 / 3 / 4,
   the liquid $10k–100k tier 1 / 1 / 2. The thin-market return is gone by
   a 7-point spread even before the look-ahead is removed.

The one cell left standing is the ITF 0.7–0.8 bin alone (+4.9% ± 2.0,
n 655) with the bins either side of it negative — one of twelve cells, not
a finding. The liquid-market *reverse* bias in the same table (favourites
over $100k winning less than priced, t −2 to −3.5) is the same look-ahead
in the other direction; the underdog strategy it implies is +5.7% ± 5.5%.

**Verdict:** no model edge, no drift, no favorite–longshot edge that
survives decision-time variables and real spreads. Tennis stays disabled in
the sim. The CLOB keeps ~30 days of history, so the next month is a free
out-of-sample test of the ITF cell: refresh the caches daily
(`market-backtest tennis --exchange polymarket` does) and re-run this
section at the end of October before believing anything.

## Result 10 (both venues): Kalshi and Polymarket agree, Kalshi leads, and the gap is not a trade

_2026-09-29. Offline, from the two caches: the games listed on both venues
in the overlapping month — 294 MLB games (6,701 aligned pre-game hours),
215 tour tennis matches (4,220 hours). Kalshi side = hourly candle bid/ask
of the home ticker; Polymarket side = the hourly last-trade series for the
same hour (one tick assumed around it). Fees: Kalshi 0.07 × mult × p(1−p)
with the series multiplier (MLB 0.5, tennis 1.0), Polymarket 0.05 × p(1−p)._

**Disagreement is small.**

| | gap sd (K − PM) | \|gap\| > 2 pts | > 3 pts | > 5 pts | Kalshi spread |
|---|---|---|---|---|---|
| MLB | 0.0079 | 4.2% | 0.8% | 0.1% | 0.010 |
| tennis | 0.0213 | 8.2% | 3.2% | 1.3% | 0.011–0.015 |

Which venue is right when they differ cannot be resolved from this: the
gaps are so small that the regression of outcome on the gap has standard
errors of 3–5 on a coefficient that should be between 0 and 1, and the two
venues' Briers agree to the fourth decimal (MLB 0.2355 / 0.2357 at 6h).

**Kalshi leads at hourly resolution.** Regressing next hour's move on the
current gap: Polymarket closes **37%** (MLB) / **71%** (tennis) of the gap
toward Kalshi per hour (± 0.01); Kalshi closes 3% / 13% toward Polymarket.
Caveat that stops this from being a finding: the Polymarket series is the
last trade, sampled hourly, so a "gap" can be a print that is fifty minutes
old, and "Polymarket converging" then means "a new trade printed". The
10-minute series (926 points per market, 57 price changes in one) could
separate lag from staleness; it has not been pulled for this.

**Trading the gap** (buy the side the other venue prices higher, on the
venue where it is cheaper, taker, hold to settlement, pooled over all
pre-game hours, standard errors clustered by game — the *optimistic*
bound, since staleness is uncontrolled):

| | θ | signals | games | ROI | se | t |
|---|---|---|---|---|---|---|
| MLB, buy on Polymarket toward Kalshi | 0.02 | 284 | 123 | −0.099 | 0.097 | −1.0 |
| MLB, buy on Polymarket toward Kalshi | 0.03 | 54 | 37 | −0.027 | 0.182 | −0.2 |
| tennis, buy on Polymarket toward Kalshi | 0.02 | 348 | 124 | +0.047 | 0.083 | +0.6 |
| tennis, buy on Polymarket toward Kalshi | 0.03 | 139 | 75 | +0.059 | 0.080 | +0.7 |
| tennis, buy on Polymarket toward Kalshi | 0.05 | 53 | 35 | +0.131 | 0.117 | +1.1 |
| tennis, one trigger per match, θ 0.03 | | 75 | 75 | +0.026 | 0.101 | +0.3 |

Buying on Kalshi *against* Polymarket's price is −27% to −31% in tennis
(t −2.4 to −2.7): the side Polymarket still prices higher is the side
Kalshi has already moved away from, and it keeps losing — lag or stale
print, either way the wrong side. A box (home on one venue, away on the
other, both at the ask, fees in) costs under $1 in 0.2% of MLB hours
(mean 1.3 points) and 1.5% of tennis hours (mean 6.9 points, max 37,
almost certainly stale prints); it needs simultaneous fills on two books
this data does not contain.

**Verdict:** no cross-venue edge measurable from history. The one fact
worth a live test is the lead-lag, and it is cheap to test properly: log
both order books for the same matches at the same seconds and see whether
Polymarket's *book* (not its last trade) follows Kalshi with a delay longer
than the time it takes to hit it.

**Addendum, 2026-09-29 03:45–04:15Z — first two-book sample.**
`sportsbot twobook-log` (substrate_bridge, data only) sampled both order
books every 20 s for 40 tennis matches listed on both venues (Asian
session, pre-match), 7,200 book rows, aligned on a 30 s grid (2,400
points). Both books were two-sided throughout: Kalshi spread 1.25 points,
Polymarket 1.17; mids changed in 12–13% of consecutive samples on each
venue. Gap sd 1.0 point, and the gap is mostly *per-pair and persistent*:
the 40 pair-mean gaps have sd 0.96 points, max 3.0.

| horizon | n | Polymarket → Kalshi | Kalshi → Polymarket |
|---|---|---|---|
| 30 s | 2,360 | +0.021 ± 0.016 | +0.020 ± 0.017 |
| 90 s | 2,280 | +0.047 ± 0.030 | +0.005 ± 0.030 |
| 180 s | 2,160 | +0.020 ± 0.047 | +0.044 ± 0.047 |
| 450 s | 1,800 | −0.002 ± 0.072 | +0.075 ± 0.073 |

Neither book closes the other's gap at any horizon from half a minute to
seven and a half: every coefficient is within two standard errors of zero
and none exceeds 0.08 of the gap. The 37% / 71% hourly "convergence" above
was, as suspected, the stale last-trade print updating, not a lagging
book. Hitting the ask when the other venue's mid sits ≥ 1 point above it
marks out at **−1.6 points on Polymarket and −2.0 on Kalshi** at every
horizon — the fee plus half the spread, with no reversion to recover it;
the signals come from 8–10 pairs whose gap simply persists.

One quiet half hour is not the last word — an active window (European
daytime tennis, an MLB slate's last hour before first pitch) is where
information would flow — so the daily check-in now logs 30 minutes at
12:00Z and re-runs this report.

**Second sample, 2026-09-29 12:01–12:31Z, European daytime tennis:** 62
matches on both venues (22 new), 12,946 book rows, 4,310 grid points.
Here Polymarket's book *does* follow Kalshi, and Kalshi does not follow
Polymarket:

| horizon | n | Polymarket → Kalshi | se (plain) | se (clustered by pair) | t (clustered) | Kalshi → Polymarket |
|---|---|---|---|---|---|---|
| 30 s | 4,248 | +0.067 | 0.021 | 0.028 | +2.4 | +0.010 ± 0.021 |
| 90 s | 4,124 | +0.139 | 0.037 | 0.054 | +2.6 | −0.035 ± 0.036 |
| 180 s | 3,938 | +0.189 | 0.053 | 0.075 | +2.5 | −0.048 ± 0.052 |
| 450 s | 3,380 | +0.355 | 0.082 | 0.173 | +2.1 | −0.106 ± 0.081 |

So the lead-lag is real in an active session and absent in a quiet one:
Polymarket's book closes about a third of its gap to Kalshi within seven
and a half minutes, Kalshi closes none of its gap to Polymarket. With
pair-clustered errors it is a 2-to-2.6-sigma result on one half hour, not a
settled fact. And it is **not a taker trade**: the gap is small (mean 0.6
points, p90 1.5, above 2 points in 4.8% of grid points), so a third of it
is 0.2–0.5 points against 1.9 points of fee plus half-spread; hitting the
lagging Polymarket ask marks out at −1.2 to −1.6 points at every horizon
(n 344–388, 13–14 pairs), and hitting Kalshi at −1.7 to −2.2.

What it would support, if it held, is the *maker* version: resting a
Polymarket bid one tick inside the gap, in the direction Kalshi already
moved, pays no fee, earns the rebate, and would capture the drift if
filled — the same adverse-selection question as Kalshi's Result 4, with a
directional signal attached.

**Third sample, 2026-09-30 12:00–12:30Z, 25 tennis matches, 4,500 rows:**
it does not hold. Polymarket → Kalshi is +0.07 ± 0.04 (30 s), −0.01 ± 0.05
(90 s), +0.07 ± 0.13 (180 s), −0.03 ± 0.09 (450 s), pair-clustered — zero
at every horizon — and the weak drift that exists runs the other way
(Kalshi → Polymarket +0.16 ± 0.05 at 90 s, +0.26 ± 0.15 at 450 s, plain
se). Three half-hours, three answers: none (quiet overnight), Polymarket
follows (Monday's active session), Kalshi drifts (Tuesday's). The
lead-lag is not stable enough to be a directional signal, so the maker
case above is withdrawn until a sample repeats Monday's pattern; the
daily check-in keeps logging. Mark-outs in this sample are within noise of
zero on 17–21 signals from 2–4 pairs — nothing to read.

**Samples 4–6 (Oct 2 22Z, Oct 3 12Z, Oct 4 12Z).** Sample 4 was dead
(Polymarket mids never moved). Sample 5 (27 pairs, active) leaned
Monday's way, +0.08 / +0.14 / +0.29 at 90 / 180 / 450 s, at clustered
t 1.3. **Sample 6** (Sunday 12:00–12:30Z, 40 tennis pairs, the busiest
session logged: mids moved in 13% / 16% of consecutive samples on Kalshi /
Polymarket) is the clearest:

| horizon | n | Polymarket → Kalshi | clustered se | t | Kalshi → Polymarket |
|---|---|---|---|---|---|
| 30 s | 2,295 | +0.104 | 0.021 | +4.9 | −0.006 ± 0.020 |
| 90 s | 2,215 | +0.230 | 0.038 | +6.0 | −0.023 ± 0.032 |
| 180 s | 2,095 | +0.358 | 0.064 | +5.6 | −0.024 ± 0.041 |
| 450 s | 1,735 | +0.496 | 0.108 | +4.6 | +0.010 ± 0.061 |

Pooled 90 s estimate across the four active samples (inverse-variance,
pair-clustered): **+0.126 ± 0.024, t 5.3**. Over six half-hours the
pattern is now consistent: when the session is active, Polymarket's book
follows Kalshi's, closing a quarter of the gap in 90 seconds and half in
seven minutes, and Kalshi never follows Polymarket. In quiet sessions
there is nothing to follow.

It is still **not a taker trade** — the gap averages 0.6 points (p90 1.0,
above 2 points in 7% of grid points), and hitting the lagging ask marks
out at −1.9 to −2.2 points on Polymarket and −2.0 on Kalshi at every
horizon in sample 6 too. The *maker* case withdrawn above is reinstated
on this evidence: a resting Polymarket bid one tick inside the gap, in
the direction Kalshi has already moved, pays no fee, earns the rebate and
would collect the quarter-to-half of the gap that closes — if it fills
before the gap does, which is the adverse-selection question only real
orders answer. That experiment (minimum size, Polymarket, tennis, active
sessions, a week of fills logged against this signal) is now the single
best-evidenced thing to spend real money finding out.

## Result 11 (both venues): other sports and categories — no arbitrage, and the cheaper venue is also the sharper one

_2026-10-02, 22:45–23:30Z. Three measurements: (a) the fee landscape of
every Kalshi series (14,577) and of every market in Polymarket's top 1,000
events by 24h volume (29,429 markets); (b) one snapshot of both venues'
order books for every game listed on **both** Kalshi and Polymarket in
six sports (`sportsbot venue-survey`, data only); (c) the repo's
cross-category `arb-scanner` run once across all 10,255 Polymarket and
136,083 Kalshi markets._

**Fees are flat on Kalshi and vary by category on Polymarket.** Kalshi:
every series is `quadratic` at multiplier 1.0 (1.75 points taker at
p = 0.5) except 18 MLB series at 0.5 and 14 fee-free one-offs (BTC/ETH
year-end ranges, annual GDP, a few Iran and Trump political binaries);
160 series — all the major game markets (NFL, NBA, NHL, EPL, UCL, tennis,
MLB) — charge makers a quarter of the taker fee, the rest charge makers
nothing. Polymarket: fees are on for 92.5% of top-event volume, at a
rate that depends on the category:

| Polymarket category | markets | 24h volume | fee rate | median spread | median liquidity |
|---|---|---|---|---|---|
| NFL | 60 | $342k | **0.03** | 0.003 | $103k |
| NBA | 113 | $273k | **0.03** | 0.001 | $54k |
| MLB | 165 | $290k | 0.03 | 0.020 | $1.9k |
| NHL | 180 | $914k | 0.05 | 0.010 | $29k |
| Tennis | 460 | $1.05M | 0.05 | 0.040 | $2.1k |
| UFC | 261 | $276k | 0.05 | 0.140 | $3.2k |
| Soccer | 3,898 | $4.27M | 0.05 / 0.03 | 0.040 | $1.0k |
| Crypto | 701 | $4.72M | 0.07 / 0.04 | 0.010 | $22k |
| Politics | 3,447 | $12.3M | 0.04 / 0.05 (88% on) | 0.040 | $2.5k |
| Geopolitics | 118 | $600k | fee-free (2% on) | 0.030 | $12k |
| Weather | 1,529 | $1.57M | 0.05 | 0.007 | $2.4k |

**Both-venue snapshot, 156 paired games** (medians; "rt cost" = spread +
two taker fees at mid, the cost of a round trip taken on that venue):

| sport | pairs | spread K / PM | taker fee at mid K / PM | rt cost K / PM | gap sd | box > 0 | best box | depth K / PM |
|---|---|---|---|---|---|---|---|---|
| NFL | 21 | 0.010 / 0.010 | 0.0163 / **0.0070** | 0.043 / **0.025** | 0.006 | 0 of 21 | −0.013 | 11.9k / 21.3k |
| NHL | 48 | 0.030 / 0.010 | 0.0173 / 0.0123 | 0.065 / **0.035** | 0.009 | 0 of 48 | −0.024 | 60 / 31 |
| NBA (preseason) | 23 | 0.050 / 0.050 | 0.0175 / 0.0115 | 0.083 / 0.069 | 0.100* | 0 of 23 | −0.019 | 935 / 31 |
| UFC | 13 | 0.010 / 0.010 | 0.0154 / 0.0108 | 0.041 / **0.032** | 0.010 | 0 of 13 | −0.013 | 5.2k / 13.8k |
| tennis | 14 | 0.010 / 0.010 | 0.0156 / 0.0113 | 0.041 / **0.033** | 0.006 | 0 of 14 | −0.0004 | 3.5k / 4.7k |
| MLB (postseason) | 12 | 0.015 / 0.010 | **0.0086** / 0.0122 | **0.032** / 0.035 | 0.008 | 0 of 12 | −0.019 | 865 / 37 |

\* The NBA "gap" is not a price gap: Kalshi's preseason books were
0.14 / 0.87 placeholders against live Polymarket quotes. Every other
sport's venues agree to within a tick, as in Result 10.

**No box anywhere.** Not one of 156 pairs lets YES on one venue plus NO
on the other be bought under $1 after fees; the closest is tennis at
−0.04 points. The cross-category `arb-scanner` (text-similarity pairing,
fees modelled as Kalshi 0.07 flat and Polymarket zero — both now wrong,
see below) matched 79 pairs; the only "edges" above 2% net are 2.4–2.7%
locked for a year or more on 3–7-cent longshots (a 2028 nominee, an IPO
"before 2027") or on questions that are not the same question ("best AI
model" against "best coding model", flagged suspect by its own
quarantine). Nothing to run money through.

**Where fees are more favourable.** On Polymarket, NFL and NBA at 0.03
are the cheapest sports markets on either venue: 0.7 points taker at the
middle, a 0.1–0.3-point spread and five-figure depth. They are also the
most efficient markets on either venue, which is why they are cheap —
the fee is lower where there is least to win. Kalshi is cheaper only for
MLB (0.5 multiplier: 0.86 vs 1.22 points at mid), and Kalshi's fee-free
series are a dozen multi-month political and macro one-offs with no
model behind them here. Polymarket's fee-free categories (geopolitics,
some politics) are likewise outside anything this repo can price. For
the US-legal path the comparison that matters is Polymarket US against
Kalshi, and Polymarket US's sports fee schedule has not been measured.

**What changes:** nothing in the sim. Adding sports does not add edge
when the price on both venues is already the same price; it adds fee
exposure. The survey is one command (`sportsbot venue-survey`) and worth
re-running when the NBA and NHL seasons are live, since both venues'
books there were thin or placeholders tonight. The `arb-scanner` fee
constants should be updated to the per-series Kalshi multiplier and the
per-market Polymarket schedule before its output is read again.

## Result 12 (Polymarket crypto Up/Down): the strategies in the X posts, measured

_2026-10-05. Three posts were put forward (@RetroValix on an HFT bot
combining spot-derived fair value, dynamic hedging and complete sets on
crypto Up/Down windows; @Dan1ro0 on a "Quant Directional Movement"
DMI/ADX system claiming +$809,704 in 214 days on BTC Up/Down; @0xNevsky on
memecoin sniping with moonbags). The third is DEX memecoin trading with an
affiliate link, outside anything this stack can measure or should touch.
The first two reduce to claims testable on real data. Markets: Polymarket
`btc-updown-5m-<epoch>` (5-minute BTC windows, fee rate 0.07 taker,
resolution = Chainlink BTC/USD 60-s TWAP at window end ≥ price at window
start), ~$40–50k volume per window, 288 windows a day._

**DMI/ADX directional signal: below a coin.** 45 days of Binance.US
1-minute BTC/USDT candles (64,257; Binance.com is geoblocked here, the
direction over a window is the same series up to venue basis), Wilder
DMI/ADX at periods 14 / 28 / 60, signal = +DI vs −DI at the window open,
gated by ADX. Break-even accuracy at a 0.50 entry with the 0.07 fee is
51.75%.

| period | ADX ≥ | 5-min n | accuracy | 15-min n | accuracy | 60-min n | accuracy |
|---|---|---|---|---|---|---|---|
| 14 | 0 | 12,281 | 0.480 ± 0.005 | 4,267 | 0.478 ± 0.008 | 1,068 | 0.493 ± 0.015 |
| 14 | 25 | 5,876 | 0.474 ± 0.007 | 2,040 | 0.470 ± 0.011 | 495 | 0.491 ± 0.023 |
| 14 | 40 | 1,539 | 0.454 ± 0.013 | 540 | 0.463 ± 0.022 | 150 | 0.460 ± 0.041 |
| 28 | 25 | 2,331 | 0.456 ± 0.010 | 820 | 0.457 ± 0.017 | 223 | 0.453 ± 0.033 |

Every cell is under 50% and the strongest trends (ADX ≥ 40) are the worst.
Five-minute direction mean-reverts slightly (48.5% of windows repeat the
previous direction), the opposite of what a trend indicator assumes.

**The wallet behind that post is real and is not that system.**
Polymarket's public data API for the linked address shows ~8,650
positions, $28.7M bought, roughly +$6.9M net, a $2,112 maker rebate and a
$309 taker rebate in a single day, 3,972 buys on Sep 24 alone at a median
$4 with prices spread from 0.09 to 0.88, and MERGE events (complete sets
redeemed). That is a two-sided market maker building sets on 5-minute
windows. Neither number reconciles with the post's +$809k, and nothing in
it is DMI/ADX.

**Spot-derived fair value: the market knows the reference price better
than a spot feed does.** A Brownian fair value P(up) = Φ(move / σ√(time
left)) at realized vol (5.2 bps a minute), fed the last *completed*
minute close before each market print (no look-ahead), on 10,079
in-window prints from 2,016 resolved windows (one week):

| minute | n | market print Brier | spot fair-value Brier | "fair > print + fee + 1 pt" rule, net per contract |
|---|---|---|---|---|
| 1 | 2,014 | 0.2313 | 0.2500 | −0.048 ± 0.012 |
| 2 | 2,016 | 0.1906 | 0.2327 | −0.030 ± 0.010 |
| 3 | 2,017 | 0.1542 | 0.2078 | −0.007 ± 0.009 |
| 4 | 2,015 | 0.1057 | 0.1805 | −0.019 ± 0.009 |
| 5 | 2,017 | 0.0453 | 0.1429 | +0.003 ± 0.012 |

A first pass of this test that used the close of the minute *containing*
the print (up to 60 s of look-ahead) showed +2 to +4 points per contract
at every minute; removing the leak removed all of it. Resolution agrees
with the Binance.US close direction in only 82.8% of windows: the
Chainlink TWAP is its own series, and the book tracks it.

**Complete-set building and mean reversion at minute prints: both lose.**
Buy the first side printing at or under T, then buy the other side if it
later prints at or under T (a $1 set), else hold to resolution:

| T | windows entered | sets completed | mean PnL per window |
|---|---|---|---|
| 0.30 | 1,988 | 14% | −0.031 ± 0.008 |
| 0.40 | 2,016 | 30% | −0.037 ± 0.008 |
| 0.45 | 2,016 | 40% | −0.047 ± 0.008 |

Plain mean reversion (buy the first side at or under T, hold): −0.014 to
−0.027 per contract (t −2 to −3). The oscillations that make sets
possible exist — 40% of windows print both sides at or under 0.45 — but
the leg you hold when the second never comes costs more than the sets
earn, and the fee takes the rest.

**Decision timing and late prints.** The winner first prints ≥ 0.97 in
minute 4 in 20% of windows, minute 5 in 42%, and only after the window
ends in 33%; most windows are open until the last minute. Late prints are
well calibrated: minute-4 prints ≥ 0.97 (n 1,340) win 99.9% at a mean
price 0.993, net **+0.005 per contract** after fee (se 0.001) — the
"near-resolution capture" the posts describe, at minute granularity and
at the last-trade price, which is an upper bound: the live book at six
seconds left showed a 28,000-contract bid already sitting at 0.999.
Whether anything is left at the ask in the last seconds is what the
seconds-level logger measures (addendum below).

**Verdict:** of everything in the three posts that can be tested, nothing
survives: the directional signal is below a coin, the fair-value rule
loses once look-ahead is removed, complete-set building and mean reversion
lose, and the one profitable wallet is a market maker. The remaining
question — a few tenths of a point in the last seconds — is a latency and
queue-position game against bots already resting at 0.999, not a
predictive edge. `sportsbot updown-log` / `updown-report` and the cached
week of windows (`data/cache/updown5m/`) reproduce this.

**Seconds-level addendum (2026-10-05 19:15–20:15Z, 11 resolved windows,
924 ticks).** The logger sampled the live book beside Coinbase spot every
3 s nominal, 3–30 s actual (each tick is three HTTP round trips from this
container). Too few windows to price the last tenths, but the mechanism
is visible: the book re-prices on 1–3 bps spot moves within a tick, and in
the window ending 19:40Z the UP contract went 0.21 → 0.77 → 0.91 while the
spot feed here still read −1.5 bps — the market saw the Chainlink move
before this feed did. In the last 30 s the winner's bid sat at 0.97–0.99
with 900–2,800 contracts behind it. The "spot-favoured side at its ask"
rule nets −23 to +10 points per contract across cells of 3–232 ticks,
which is noise. Measuring the last-seconds edge needs a sub-second
Chainlink-stream feed and co-located execution; from here it is not
measurable, and the queue already resting at 0.999 says who is taking it.

## Result 13 (other venues, arbitrage classes, HFT): what the wider market offers a $100 US account

_2026-10-05 21:00–22:30Z. Scope: every other venue with a reachable
public price feed from this environment, every arbitrage class the
literature documents, and the practical question of high-frequency
execution from this stack. All live snapshots; the paper cited is
Saguillo, Ghafouri, Kiffer & Suarez-Tangil, "Unravelling the Probabilistic
Forest: Arbitrage in Prediction Markets", arXiv 2508.03474 (IMDEA, Aug
2025)._

**Venues.** Of the exchanges a 2026 US account could use or read,
reachability and fees from here:

| venue | public read API | US-tradeable | fee | what it lists |
|---|---|---|---|---|
| Kalshi | yes | yes (CFTC) | 1.75 pts taker at mid (MLB 0.875); makers ¼ on game series | sports, politics, econ, weather, crypto |
| Polymarket (main CLOB) | yes | reads only; orders geoblocked | 0.03–0.07 by category, taker only | everything |
| Polymarket US | no public feed found | yes (Dec 2025) | not measured | sports |
| PredictIt | yes (`marketdata/all`) | yes, $850/contract cap | **10% of profit + 5% on withdrawal** | US politics only |
| Smarkets | yes (v3, unauthenticated reads) | no (UK) | 2% commission | sports, politics |
| Betfair | needs app key | no | 2–5% | sports |
| ProphetX, Novig, Sporttrade | no public API | yes (ProphetX 49 states) | p2p sports exchange | sports |
| ForecastEx (IBKR) | IBKR API only | yes | small, contracts accrue interest | econ, climate, politics |
| Overtime, Azuro, Limitless, Myriad | 401 / moved / 404 / empty | no (on-chain, non-US) | on-chain | sports, crypto |
| Manifold | yes | play money | none | everything |

**Arbitrage class 1 — dutch books within a venue.** Every multi-outcome
event on both venues, live: Polymarket's 19 fully two-sided negRisk
events (of 222 scanned, 1,229 books read) have a cheapest buy-all of
**1.0075** (a 3-way soccer line) and a median of 1.0475 after fees; no
event under $1 in either direction. Kalshi's 87 fully two-sided
mutually-exclusive events: 17 have YES asks summing under $1, but those
sets are not exhaustive ("51st state", "next pope"), so the sum under $1
is not a box; the exhaustive direction (NO on every leg) is above fair in
all 87. Zero dutch books.

**Arbitrage class 2 — the same question on two venues.**
*PredictIt vs Polymarket*, 35 matched 2028 contracts (both nominations
and the presidency): PredictIt quotes most names 1–5 points above
Polymarket, but after its 10% profit fee and 5% withdrawal fee **0 of 35
boxes are positive**; ignoring the withdrawal fee 17 would be, by 0.1–2.5
points, locked until 2028 under an $850 cap. *Smarkets vs Polymarket*,
23 matched tennis players tonight: on true matches the mid gap is within
±1.5 points and every box is −2 to −5 points; the four apparent +36 to
+71-point "boxes" are surname collisions (Zheng, Sun, Guo, Wang) between
different players, which is the standard failure mode of text-matched
cross-venue arbitrage and why the repo's `arb-scanner` quarantines big
"edges" as suspect. Kalshi vs Polymarket is Results 10–11: within a tick.

**Arbitrage class 3 — combinatorial (dependent markets).** The paper's
inequality: a box exists when a candidate's presidency YES bid exceeds
their nomination YES ask (buy nomination YES, buy presidency NO; payoff
≥ 1 in every state). Across 29 candidates priced on both Polymarket
events tonight: 4 positive by 0.03–1.04 points, all at 1–2-cent prices,
two of them last-name collisions (Trump / Trump Jr., two Johnsons). The
paper itself found 13 dependent pairs in a whole election year with
"average max profit around $100" per pair.

**What the literature actually measured.** The $39.6M headline is gross
extraction over April 2024–April 2025 on **86 million bets**, when
Polymarket charged **no fees**, concentrated in 2024-election politics;
the median profit per within-condition opportunity was **$0.60**; the top
wallet made $2.0M over 4,049 transactions; execution is non-atomic (one
leg can fail). Polymarket now charges 0.04–0.07 on exactly those
categories, which is larger than the median opportunity. The marketing
pages found by search ("9.6% average arb rate", "22% risk-free") are
affiliate funnels for bot subscriptions and do not survive the fee
arithmetic above.

**High-frequency execution from this stack.** Request latency from this
container is 0.2–1.3 s per call; the Up/Down logger could not hold a 3 s
cadence; Polymarket main-CLOB orders are geoblocked for US IPs and that
is not something to route around; Kalshi has no co-location. Result 12's
seconds-level sample showed the Up/Down book moving before the spot feed
here. Anything labelled HFT in the posts is a latency race this stack
cannot enter, and the fee at 0.5 (1.25–1.75 points) is larger than the
sub-second mispricings the race is for.

**Verdict:** adding venues adds price references, not profit. Every
arbitrage class is either closed (dutch books, cross-venue), eaten by a
fee schedule the literature pre-dates (PredictIt, Polymarket
post-2026), or a name-matching artefact. The two things in this document
with real evidence remain: fee asymmetry favours resting orders on both
venues, and Polymarket's tennis book follows Kalshi's in active sessions
(Result 10, pooled t 5.6). Both point at the same experiment — resting
maker orders on Polymarket US or Kalshi, minimum size, logged against the
Kalshi signal — and nothing measurable from here advances it further.

## Result 14 (Polymarket MLB): informed taker flow exists, and copying it does not pay

_2026-10-08 22:30–23:15Z. Prompted by three X posts on "copy the smart
wallets" (memecoin bots, influencer-wallet tracking). The claim reduces
to two questions on the public taker tape (`data-api.polymarket.com/
trades`, `takerOnly=true`; 685 resolved MLB moneylines, 60 days, 760,382
taker fills; `sportsbot wallet-follow`). Everything is PRE-GAME: the
close is the last pre-start print (rail-guarded), so an in-play fill has
no closing line and a copier could not match in-play latency anyway.
Wallets are ranked on the first 70% of games by start time and graded
only on the last 30% — ranking on the trades you then copy is
survivorship dressed as skill. CIs are percentile bootstraps clustered by
market. Fee: the MLB moneyline `feeSchedule` on Gamma reads rate 0.05
(checked live 2026-10-08; the 0.03 in Result 11 is the futures/champion
market's rate), so the copy leg pays 0.05·p(1−p) plus half a one-tick
spread._

**1. Is taker flow informed?** As a population, no: takers pay over the
close. Own-price CLV of every pre-game taker trade: ranking window
**−0.0033 [−0.0045, −0.0021]** (n 131,832, 479 markets); test window
**−0.0014 [−0.0028, −0.0001]** (n 55,609, 206 markets). That is the
"takers lose to makers" result from the Kalshi literature reproduced on
Polymarket MLB, at a third of a point.

**2. Can the best wallets be copied?** Two selections of 20 wallets from
the ranking window (≥ 20 trades over ≥ 5 markets; 1,023 of 18,034 wallets
qualified), graded on the test games:

| selection | test trades | own-price CLV | next-print CLV (no costs) | copy net CLV (30 s) | copy net CLV (300 s) |
|---|---|---|---|---|---|
| top 20 by realised profit/$ (what a leaderboard shows) | 63 | +0.0008 [−0.0041, +0.0055] | +0.0056 | **−0.0114 [−0.0159, −0.0069]** | −0.0126 |
| top 20 by closing-line value | 966 | **+0.0101 [+0.0073, +0.0132]** | **+0.0100 [+0.0073, +0.0124]** | **−0.0071 [−0.0098, −0.0047]** | −0.0092 |

Three things in that table:

* **Leaderboard selection finds nothing.** The wallets with the best
  realised profit per dollar in the ranking window barely trade out of
  sample (63 fills) and carry zero CLV there. Profit over a few weeks of
  binaries is luck; it does not predict the next weeks.
* **CLV selection finds real information.** The 20 best-CLV wallets keep
  **+1.0 point at their own price out of sample** (t ≈ 6.7), and the
  information is still there at the next print 30 s later (+1.0) and
  five minutes later (+0.8): these trades predict where the line closes,
  not merely stale quotes they happened to hit.
* **It is not copyable as a taker.** Crossing the spread 30 s behind
  them and paying the 0.05 fee nets **−0.7 points** (CI −1.0 to −0.5).
  The signal survives the delay; the costs eat it. At the 0.03 rate the
  futures markets carry, the same copy would net −0.2 [−0.5, +0.0] — a
  lower bound on how far it is from working, not a result.

**Who it is.** 572 of the 966 test trades (59%) come from one wallet
(`0x2a69…`, public name "UpTheBlues"): 822 pre-game taker fills across
87 of the 685 games, every one a BUY in a uniform ~$265 clip, median 1.5
hours before first pitch, median price 0.47, $195k deployed on MLB in 60
days, +0.8 points CLV out of sample. That profile is a sharp-line taker
in fixed clips — the strategy this repo's rank-1 harness grades, run by
someone else — and its gross +0.8 is about the taker fee at mid. The
other informed wallets are 40–60 fills each at +1.3 to +1.9 points. The
remaining 6 of 20 traded nothing in the test window.

**What it changes.** Nothing for a taker, and one thing for the maker
plan. "Copy the smart wallets" is dead in the form the posts sell it:
leaderboard wallets carry no information and informed wallets cannot be
followed across the spread. But the +1.0 point that survives at the next
print is exactly what a resting order in the informed direction would
collect without crossing or paying the fee — if it fills before the line
moves, which is the adverse-selection question rank 2 exists to answer.
Informed-flow direction therefore joins the sharp line as a second
candidate signal for the maker-only experiment, data-logged alongside it;
it earns no stake on its own, least of all one wallet's.

**Parity check (same session).** The Polymarket market backtest can now
run the bot's own `evaluate_market_verbose` under `config/default.yaml`
(`--policy live`) instead of the harness's own rules: MLB 303 priced, 4
bets, mean CLV −0.005; tennis 9,572 resolved / 7,989 priced, 422 bets,
mean CLV −0.0062 (24% CLV-positive, paper ROI +7.9% on a 36% hit rate).
Same shape as Results 8 and 9: the live policy would not have done
better. The maker variant also gained a strict fill model (a later
pre-start taker print must trade through the resting bid, from the same
tape), so "fills assumed" is no longer the only maker number.

## Live paper record so far (for the record, not for inference)

After the sizing fixes of 2026-09-27 the $100 sim has settled 9 bets on two
MLB games: realized +$0.63, **mean CLV −0.0017**. n = 9 says nothing about
edge in either direction; it is logged here so the running tally has a
starting point, and because a CLV of zero on the first nine is exactly what
Results 1–3 predict.

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
- **Out: trading the weather forecast.** Beta 0.036 (t 0.43) against the
  price; the market already contains it. It remains the substrate's
  conventional baseline, which it beats climatology for.
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
