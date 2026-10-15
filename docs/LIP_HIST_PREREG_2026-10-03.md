# Kalshi LIP: historical fill-loss vs reward test — pre-registration (2026-10-03)

Committed before any candle or trade data for this test was pulled. Replaces the
forward recorder as the first answer, because this container cannot keep a
recorder alive; the forward run on the server remains the confirmation.

## Question
Do resting quotes on rewarded Kalshi markets lose less to adverse selection than
they earn in Liquidity Incentive Program rewards?

## Data
- Programs: `/incentive_programs?status=paid_out` with end dates 2026-09-01 …
  2026-10-02 whose market has settled. Stratified random sample (seed 11): up to
  300 programs per category for the 10 largest categories.
- Quotes: 1-minute candles over the program window (yes bid / yes ask close).
- Fills: the trade tape over the window. Settlement: `settlement_value_dollars`.

## Simulated policy (per program, Q = 100 contracts per side)
- Every minute, rest a YES bid at the previous minute's best YES bid and a NO bid
  at 1 − previous best YES ask. Only when both quotes exist and the spread is
  ≤ 0.10.
- Fill rules (both reported):
  - **through:** a later trade in that minute prints strictly through our price.
    Our level was consumed, so the fill is certain.
  - **touch:** a trade at or through our price. Ignores queue position, so it
    shows the most fills.
- Each fill = Q contracts at our price, held to settlement. The side is re-posted
  the next minute (continuous quoting, which is what earning rewards requires).
- Fill P&L = Q · (payout − price); makers pay no fee on `quadratic` series, and
  0.0175·mult·p(1−p) on `quadratic_with_maker_fees`.
- Reward pool = `period_reward` × 1e-4 USD (unit assumption, stated).

## Share
- Our share of the pool is not observable historically.
- Report the **break-even share** s* = fill loss / pool, per category: sum over
  programs.
- Estimate the share we would actually get from live order books: the median
  share of a 100-lot quote joined at the best bid on each side, under Kalshi's
  scoring rule, over the 3,597 snapshots recorded 2026-10-02/03, by category.

## Pass bar, per category, under the *touch* rule
- Net = pool × observed median share − fill loss, using one observation per
  program, clustered by end date.
- Required in both halves (Sep 1–16 and Sep 17–Oct 2): mean net > 0 with
  day-clustered t ≥ 2, and s* < observed median share.
- The *through* rule is reported for context only; it does not decide pass/fail.

## Known limits (disclosed)
- Shares are measured in October and applied to September.
- Our own presence would change other participants' quoting.
- Minute bars can miss intra-minute quote moves.
- The reward unit is an assumption; a wrong unit changes pool size by a power
  of ten, which would flip the verdict only near break-even.
