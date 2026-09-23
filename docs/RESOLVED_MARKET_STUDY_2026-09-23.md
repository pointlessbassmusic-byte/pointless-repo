# What Polymarket's resolved history says about price-only edges

Study: `polymarket-edge/src/calibration_study.py`. Data: 1,327 resolved Yes/No
markets with volume >= $10k (Gamma reaches ~2,100 by volume before its offset
cap), 7,278 sampled prices from the data API's trade log, run 2026-09-23.
Per-row data in `polymarket-edge/docs/calibration_rows.csv`.

## Method, and the trap it avoids

Price at each horizon is the last trade at or before **scheduled end minus h**,
normalised to the YES side. Outcome is the final `outcomePrices`.

The first run measured horizons from `closedTime` and it was wrong in a way
that looked like edge. A "will X happen by <date>" market that resolves YES
closes *when X happens*, so "24h before close" on those rows is "24h before
the event", when the price was still low. That manufactures underpriced
longshots. Measured from the scheduled end instead, and dropping markets whose
scheduled end is a far-future placeholder (>30 days from close), the
longshot effect disappears.

## Result 1: no model-free favorite or longshot edge

Buy whichever side trades at or above a threshold, hold to resolution. Return
per $1 staked, gross of spread; stderr in parentheses.

| horizon | >= 0.80 | >= 0.90 | >= 0.95 |
|---|---|---|---|
| 1h | -0.9% (0.6) n=981 | -0.4% (0.4) n=900 | -0.5% (0.3) n=854 |
| 24h | -1.6% (0.6) n=957 | -1.0% (0.5) n=872 | -0.8% (0.4) n=805 |
| 72h | -1.8% (0.7) n=926 | -1.0% (0.5) n=827 | -0.6% (0.4) n=766 |
| 168h | -2.0% (0.8) n=869 | -1.6% (0.6) n=772 | -0.5% (0.4) n=707 |
| 720h | -1.7% (1.0) n=684 | -2.1% (0.8) n=571 | -0.1% (0.5) n=489 |

Favorites lose at every threshold and horizon, before paying the spread. The
mirror trade (buy the cheap side) is not the answer: with a 1c spread it runs
-45% to -75% per $1 at t = -4 to -9, and even gross of spread it is negative
with a standard error too wide to say anything. Liquid Polymarket markets are
calibrated to within noise across the middle of the price range (every 10c
bucket's realized rate sits inside its 95% interval at 24h), with favorites
slightly rich. There is nothing here for a taker to harvest from price alone.

## Result 2: the split by category

Favorites >= 0.80, 1c spread, 24/72/168h pooled:

| category | n | mean | t | losses | markets |
|---|---|---|---|---|---|
| fed (FOMC decision buckets) | 78 | **+4.7%** | **+7.3** | 0 | 75 |
| fifwc (World Cup 2026) | 145 | -4.1% | -1.2 | 21 | 196 |
| what / us / iran / israel (news) | 189 | -11% to -37% | -2.4 to -3.7 | 44 | 179 |

Two other positive rows (`pennsylvania`, `who`) are 2024-election county and
"who will win" markets: one event, not repeatable.

The split has a mechanism. Fed decisions have a public reference price —
fed-funds futures — that is sharper than the book, and nobody arbs the last
2-3c the week before a meeting because the absolute yield is small. News and
geopolitics favorites have no such anchor and the crowd overprices certainty.

## Result 3: the Fed pattern, meeting by meeting

15 FOMC meetings, Sep 2024 to Sep 2026, 75 bucket markets. Favorites >= 0.90
with a 1c spread:

| horizon | n | mean | se | losses | avg price |
|---|---|---|---|---|---|
| 1h | 22 | +1.8% | 0.5% | 0 | 0.973 |
| 24h | 22 | +2.1% | 0.5% | 0 | 0.970 |
| 72h | 24 | +2.6% | 0.5% | 0 | 0.965 |
| 168h | 21 | +3.4% | 0.6% | 0 | 0.958 |
| 720h | 24 | +3.5% | 0.7% | 0 | 0.957 |

At >= 0.80 the 720h row has one loss (a favorite at 0.92 a month out that
flipped), so the calendar matters: the book converges in the last week.

Every one of these is the *decision* bucket ("25bp cut", "no change") trading
at 0.87-0.98 in the final days while futures already implied it. The
`>25bp hike` NO-sides at 0.999 are also in the sample and pay nothing;
the tradeable set is the decision bucket.

## The rule, and the risk

Within 7 days of an FOMC meeting, when the decision bucket trades at >= 0.90
on either venue and the reference (fed-funds futures / the other venue) agrees,
buy it and hold to resolution. Historical: 50 of 50 paid, +2.1% at one day,
+3.4% at one week, net of a 1c spread. Kalshi (`KXFEDDECISION-<mmmYY>`) lists
the same buckets, is the US-legal venue, and charges ~0.2c at 97c.

This is a short-volatility trade and must be sized as one. A Fed surprise
costs the whole stake; at +2.5% per win the breakeven surprise rate is ~2.4%.
The last decision-week surprise of that size was June 2022 (75bp after 50bp
was priced until a leak two days out) — roughly one in a hundred meetings.
One loss erases forty wins. Cap the stake at what a total loss would not
change.

Live on 2026-09-23 the October meeting is a coin flip on both venues (hike
25bp 0.50-0.53, no change 0.46-0.48), so there is nothing to buy yet. That is
the pattern: the edge does not exist a month out.

## What this means for the engines

- polymarket-edge's sportsbook-consensus thesis looked like the same *class*
  of edge as the Fed pattern (a sharper external reference than the book).
  Measured the same day on 1,048 settled soccer matches
  (`docs/SPORTSBOOK_VS_POLYMARKET_2026-09-23.md`), it is not: Polymarket an
  hour before kickoff is as sharp as every sportsbook closing line, and the
  book's side of a disagreement loses. The reference has to lead the market;
  fed-funds futures do, a consensus of retail books does not.
- The Fed rule itself now runs as a recorder in arb-scanner
  (`src/fed_watch.py`, `python -m src.fed_watch --report`): both venues, the
  three gates above, first-fire entries, settlement from Kalshi. It trades
  nothing; it exists to build the settled record the pre-live gate needs.
- Weather is the opposite class: the book is the sharper source. Settled.
- Price-history generators (mean reversion, momentum) are taker strategies on
  price alone; this study says the base rate for those is zero or negative.
  Treat any backtest that finds edge there as suspect until it survives
  settled outcomes.

## Addendum, same day: out-of-sample checks and the regime caveat

**More meetings.** The volume-ranked pull started at Sep 2024. Polymarket's
per-meeting decision markets begin at Mar 2024; the three earlier meetings
(Mar, Jun, Jul 2024 — all holds at 0.95–0.985 the day before) paid +2.45% at
24h and +1.9% at 168h with no losses. The sample is now **18 meetings, 0
losses at >= 0.90 within a week**. There is no earlier public history on
either venue: Kalshi's legacy `FED` series is not served by its API, and no
Fed-like market appears in windows around any 2022–2025 meeting. June 2022,
the one known decision-week surprise, is therefore unobservable here; the
tail-risk estimate stays an argument, not a measurement.

**Kalshi, at real bid/ask and fees.** `KXFEDDECISION` and `KXFED` serve two
settled meetings (Jul and Sep 2026), hourly candlesticks. Both decision
buckets paid — no-change at 0.80 ask a day before July (+25%), hike-25 at
0.87 a day before September (+14%) — and both were priced by Polymarket within
a cent of Kalshi. That is confirmation of direction, and of cross-venue
agreement, and nothing more: two events.

**The caveat those two events expose.** In 2024–25 the decision was
telegraphed and the bucket sat at 0.95–0.99 a day out: a certain 1–3c. In
2026's contested hiking cycle the favorite sat at 0.80–0.88 a day out and a
coin flip a week out, so the >= 0.90 rule would not have fired on either
venue, and the 14–25% it left on the table came from a bucket that was
*genuinely* uncertain. Whether Fed favorites in the 0.80–0.90 band are also
underpriced is untested — six rows across both venues, all winners, which is
not evidence. Do not lower the threshold on the strength of two meetings.

So the rule stands as written, with its regime named: it earns 1–3c per
meeting when the decision is telegraphed and stands aside when it is not.

## What 18 meetings can and cannot establish

PR #22's power analysis applies here too, and it cuts against the headline.
The t = 7.3 is the t-statistic of the *mean return given that no loss
occurred*; it says nothing about how often a loss occurs, which is the only
number that matters for a trade that wins 2c and loses 100c. Zero losses in
18 independent meetings bounds the surprise rate at about 16% with 95%
confidence (rule of three: 3/18). Breakeven is 2.4%. The sample cannot rule
out a loss rate six times breakeven.

What carries the claim is the mechanism and its longer record, not this
sample: fed-funds futures have priced the decision at >= 90% the week before
in the large majority of meetings since the Fed began pre-announcing in the
1990s, and decision-week reversals of a >= 90% pricing are rare enough (June
2022 is the recent one) that a surprise rate in the low single digits is the
defensible prior. That is an argument from outside the data. Treat the 18/18
as consistent with it, not as proof of it, and size the position accordingly.

The maker variant tested in PR #22 is spread capture against sports flow.
Exchange-paid liquidity rewards (Polymarket's `clobRewards`, paid for resting
orders inside `rewardsMaxSpread` regardless of fills) are a different
mechanism and are untested here; they carry inventory risk and are not an
edge claim.
