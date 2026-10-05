# Follow the sharps: copying wallets that were profitable on Polymarket sports — pre-registration (2026-10-05)

Committed before any wallet or trade data for this test was pulled.

## Hypothesis
Wallets that were profitable on Polymarket sports moneylines in August keep being
right in September by enough that copying their buys after a delay, at the
then-market price and paying fees, makes money. This is an information signal
(trader identity), the one class not tested so far.

## Markets
- Polymarket events tagged `sports`; markets with `sportsMarketType = moneyline`.
- Only binary YES/NO resolutions (an outcome price of 1/0).
- Period A (selection): events ending 2026-08-01 … 2026-08-31.
- Period B (test): events ending 2026-09-01 … 2026-09-30.

## Selecting sharps (period A only)
1. Candidate pool: wallets appearing in the latest ≤ 2,500 trades of each
   period-A moneyline market (data-api `/trades?market=`), with at least $500 of
   buys in that sample.
2. For each candidate (top 1,500 by sampled buy volume), pull the full August
   activity (`/activity`, paged by time). Per-market P&L on period-A moneylines =
   −buys + sells + redemptions. Markets must be resolved.
3. Exclude market makers: any MERGE activity, or both outcomes bought in > 30% of
   their markets.
4. Sharp = ≥ 20 period-A markets and a per-market P&L t-stat ≥ 2.

## Copy rule (period B)
- Signal: the first BUY by any sharp in a period-B moneyline market, one entry per
  market: that outcome, at time t.
- Entry price = max(their fill price, CLOB price-history value at t + 60 s)
  + 0.005, capped at 0.99.
- Taker fee 0.05 · p(1−p) (Polymarket sports fee rate). Held to resolution.
- Upper-bound variant (reported, not used for pass/fail): entry at their exact
  price, with fee.

## Pass bar
- ≥ 200 entries, mean P&L > 0, day-clustered t ≥ 2, and ROI ≥ 1% of cost.
- Also reported: the same rule with sharps selected at random from the candidate
  pool (placebo), and the per-sharp distribution.

## Compliance
- Research signal only.
- Main-CLOB order placement is geoblocked for US IPs and never circumvented.
- If this passed, execution would go through Polymarket US or a matched Kalshi
  market, which would need its own test.
