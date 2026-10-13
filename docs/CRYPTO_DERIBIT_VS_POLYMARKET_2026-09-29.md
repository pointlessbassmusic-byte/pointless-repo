# Does Deribit's option market lead Polymarket's BTC/ETH price digitals? No.

`python -m src.crypto_study` in polymarket-edge (`--analyse` rescoring the
committed rows offline, `--reprice` recomputing the Deribit side from the
cached series); rows in `polymarket-edge/docs/crypto_rows.csv.gz`. No key
needed: Gamma, the Polymarket data API and Deribit's public endpoints.

## The question

The reference-price class has one confirmed member, fed-funds futures
against Polymarket's Fed decision buckets, and four measured non-members
(sportsbook consensus, the other venue, in-house Elo, the Cleveland Fed CPI
nowcast). What separated the Fed case looked like this: the reference is
itself a deep market, not a model the crowd already reads. Deribit's BTC and
ETH option market is the deepest such candidate, and Polymarket lists the
matching event daily: "Will the price of Bitcoin be between $X and $Y /
above $X / below $X on <date>?", settled on the Binance 1-minute close at
noon ET, in 8-15 buckets per day per asset with real volume (median $43k
per digital). If option-implied volatility carries information the digital's
crowd lacks, this is where it would show.

## Method

- **Digitals.** Every settled BTC and ETH fixed-time digital under Gamma's
  `bitcoin` and `ethereum` tags, March 2024 - September 2026, discovered in
  weekly `end_date` windows (Gamma refuses offsets past ~2100 and the tag
  has thousands of closed events). Touch markets ("reach", "dip") are
  excluded. Two wordings exist: until May 2025 the description carried the
  full settlement stamp; since then the time is in the description and the
  date in the title, with the year taken from the event's `endDate` and the
  read refused if it lands more than two hours from it. 17,436 digitals in
  1,795 events; 11,467 of the 1h rows are from 2026.
- **Reference.** Zero-drift lognormal digital probability from Deribit's
  hourly perpetual close and hourly DVOL (the 30-day implied-vol index),
  with a control model using 30-day realised hourly vol instead of DVOL.
  Sampled 1h, 6h and 24h before settlement.
- **Market.** The last Polymarket trade at or before the same instant,
  normalised to the YES token, with its age recorded (`pm_age_min`). A
  print from hours earlier is not a price anyone could trade at, so every
  result is also shown on prints at most 10 minutes old.
- **Scores.** Paired Brier with a bootstrap over events (all of an event's
  buckets move together), a linear probability regression of the outcome on
  both probabilities, the take-the-reference-side return after a 1c spread
  at 5/10/20-point disagreements, the Fed-style >= 0.90 favourite rule, and
  the lead-lag regression of the later price move on the earlier gap.

## Results (52,106 rows)

| horizon | n digitals | Brier Polymarket | Brier DVOL-implied | diff (95% CI) | beta price / beta DVOL |
|---|---|---|---|---|---|
| 1h | 17,425 | 0.0166 | 0.0164 | +0.0002 [-0.0003, +0.0007] | +0.41 (t 14) / +0.59 (t 21) |
| 6h | 17,405 | 0.0353 | 0.0355 | -0.0001 [-0.0006, +0.0004] | +0.54 (t 15) / +0.45 (t 12) |
| 24h | 17,276 | 0.0510 | 0.0510 | +0.0000 [-0.0005, +0.0006] | +0.50 (t 11) / +0.51 (t 11) |

The two are indistinguishable at every horizon on 17,000 digitals, and the
realised-vol control scores the same as DVOL (within 0.0002). The
regression splits the weight roughly in half: both carry information, the
average beats either, neither dominates. Median disagreement is 0.1 of a
cent at 1h and 6h, 0.4c at 24h; the 90th percentile is 3-5 points.

**On prints that were actually current, the market is slightly better.**

| horizon | fresh prints (<= 10 min) | Brier diff | DVOL side at 5-point gap |
|---|---|---|---|
| 1h | 6,347 | -0.0011 [-0.0020, -0.0002] | n=894, -0.8%/$1 (se 7.1) |
| 6h | 6,699 | -0.0014 [-0.0023, -0.0004] | n=923, +3.7%/$1 (se 6.9) |
| 24h | 5,921 | -0.0009 [-0.0017, -0.0001] | n=741, -17.0%/$1 (se 6.7) |

On stale prints (> 60 min old, about 40% of rows) the DVOL side scores
better by +0.001 and the strategy shows returns of several hundred percent
on 56-143 rows: that is a settled or moved market whose last print never
updated, not a price. The all-print strategy returns (+50% at 1h, +28% at
6h, +8% at 24h) are those rows.

**The Fed-style favourite rule has no footprint here.** Digitals priced
>= 0.90 pay 99.2% of the time and return -1.0% at 1h, -1.3% at 6h and
-0.5% at 24h after a 1c spread, on 3,400-4,400 rows per horizon. Fairly
priced.

**Lead-lag** on all prints says the price moves 37% of the gap toward the
DVOL value (t 10-14), the same reading the venue study gave before PR #33's
order-book sample showed it was the stale print updating. Restricted to
prints fresh at both horizons it drops to +0.20 (t 1.8) from 24h and +0.28
(t 4.2) from 6h. A positive coefficient survives partly by construction: any
transient noise in the earlier print enters the gap with one sign and the
later move with the other. The two tests immune to that, paired Brier and
the strategy return on fresh prints, both say the market is at least as
good.

BTC and ETH, low- and high-volume halves, 2025 and 2026 all read the same
way within their intervals; the only sub-sample where DVOL is better is
ETH at 1h (+0.0010 [+0.0001, +0.0020]) and BTC there goes the other way
(-0.0006 [-0.0011, -0.0001]).

## The result this replaces

The first pass of this study reported a 1h Brier of 0.0063 for the DVOL
model against 0.0166 for the market, a +122%/$1 return on fresh prints at a
5-point gap with t ~ 8, and 68-81% hit rates. Every one of those numbers was
a look-ahead. Deribit stamps a candle by its **open** time (checked against
1-minute candles: the hourly bar stamped 15:00Z closes at the 15:59 print),
so the "last close at or before 15:00Z" was the 16:00Z price, and for a
digital settling on the 16:00Z Binance close that is the settlement price.
`HourlySeries` now keys every bar by its close time and a test pins the
convention. The corrected numbers are the tables above. A result that good
against a liquid market is a leak until the leak is found; this one took a
one-line check against a finer resolution.

## What this means

- Deribit's option market does not lead Polymarket's BTC/ETH digitals. A
  crude lognormal on public spot and DVOL prices these markets exactly as
  well as the crowd does, and on current prints the crowd is a hair better.
  There is nothing here to trade, at any of the three horizons, before any
  fee.
- The reference-price class is now one confirmed member and five measured
  non-members. The Fed case is not just "a deep market as reference": DVOL
  is one too. What the Fed case has that this does not is a reference that
  prices the **identical event** (a decision, not a distribution over the
  underlying) and a prediction-market crowd that is thin next to it. The
  BTC digital's crowd is crypto-native and watches the same spot and vol.
- The favourite footprint that pays on Fed buckets and paid on 12 CPI rows
  does not exist on 4,400 crypto rows. Whatever makes the last 3c cheap on a
  Fed bucket is not a general property of >= 0.90 prices.

## Caveats

- Deribit's perpetual is not Binance's BTC/USDT spot; the basis is a few
  basis points against buckets thousands of dollars wide.
- Zero drift and a flat 30-day implied vol are the simplest model for a
  1-24h digital; a smile-aware, term-matched model would be sharper. Since
  the crude one already ties the market, a better one could only show the
  market is beatable by a margin the crude one hides, and the realised-vol
  control tying as well argues against much room.
- The market price is the last trade, not the book. Fresh-print rows bound
  the staleness at 10 minutes; a 10-minute-old print an hour before a
  1-2% wide bucket settles can still be a point or two off the book.
- No fee is applied; Polymarket's crypto markets may carry a taker fee,
  which only lowers the strategy rows.
- The strategy rows buy at the print plus 1c; a real order pays the ask on
  a book this study never saw.
