# Maker fills held to settlement, split by VPIN — pre-registration (2026-10-07)

Proposed as "rank 3" in `docs/USER_RESEARCH_Beating_prediction_market_prices.md`,
following Bartlett & O'Hara: makers earn by underwriting YES-overbetting at
settlement, and lose when trailing one-sided flow (VPIN) is high. Committed before
any trade data for this test was pulled.

## How this differs from earlier tests
- SURVEY_2026-10-01 and LIP_HIST simulated a *new* order joining the back of the
  queue: the marginal, slow maker.
- This test scores *every actual maker fill* on the tape, i.e. the average maker
  (including fast incumbents).
- A positive result means a pool of profit exists. Whether a new entrant can get
  queue priority to reach it is a separate question.

## Data
- Settled Kalshi markets closing 2026-08-05 … 2026-10-05.
- Series groups and maker fees:
  - **ATP/WTA** (KXATPMATCH, KXWTAMATCH): maker fee 0.0175·p(1−p).
  - **Challenger/ITF** (KXATPCHALLENGERMATCH, KXITFMATCH): no maker fee.
  - **MLB** (KXMLBGAME): maker fee 0.0175 × 0.5 × p(1−p).
- Fee rounded up to the cent per fill (per trade print, as an order-level proxy).
- Random sample (seed 41) of up to 300 markets per series; full trade tape for each.
- **Pre-match only:** fills before the tape-defined in-play cut
  (`sportsbot.backtest.markout.prematch_cut`, jump 0.10, min 10 fills).
  Markets the cut cannot classify are skipped.

## Measures
- Maker P&L per contract held to settlement. The maker is the opposite side of
  the taker:
  - taker bought YES at y → maker bought NO at 1−y → P&L = y − settle − fee;
  - taker bought NO → maker bought YES at y → P&L = settle − y − fee.
- **VPIN** at each fill:
  - Per market, volume buckets of size (total pre-match volume / 50).
  - Each bucket's |YES-taker − NO-taker volume| / bucket size.
  - VPIN = mean over the last 20 *completed* buckets; fills before 20 buckets
    have completed are "warm-up".
  - Groups: VPIN < 0.86, VPIN ≥ 0.86, warm-up.

## Unit and pass bar
- One observation per market: the contract-weighted mean of that market's fills in
  the group. t-stat across markets.
- Halves: closes Aug 5 – Sep 4 and Sep 5 – Oct 5.
- A series group passes ("passive quoting has a pool") if VPIN < 0.86 is
  > 0 after fees with t ≥ 2 in **both** halves.
- If the VPIN < 0.86 group is ≤ 0 in both halves, passive quoting on that series
  group is declared dead.
- Also reported: the VPIN ≥ 0.86 group, the share of P&L from the top 2% of
  fills, and the YES/NO taker split.
